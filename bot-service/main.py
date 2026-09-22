"""
Nexora AI — the bot service.

This is the retrieval half of the product, and it stayed in Python because
that is where it belongs: sentence-transformers for embeddings, the numpy dot
product that scores them, pandas and friends for reading whatever spreadsheet
a customer uploaded, and the grounded-summarisation pipeline on top. Every one
of those is a Python library with no equivalent worth the migration.

The Node backend owns everything else — accounts, sessions, billing, credits,
referrals, the admin panel, support and the dashboard assistant.

Where the line is
-----------------
**Node is the only thing the internet talks to.** It authenticates the request
(session cookie or ``X-Api-Key``), meters credits, resolves *which account this
is*, and then calls this service over loopback with a plain ``user_id``. So
nothing here checks a password, reads a cookie, or knows what a credit is — by
the time a request arrives, all of that is settled.

That is why these routes take ``user_id`` as an ordinary parameter and trust
it. It is not a credential and was never sent by a browser; it is Node's
answer to "who is this", and the only way to reach these endpoints is through
Node. :func:`require_internal_key` is the belt to that braces.

**Collections are owned, not shared.** This service writes ``bots``,
``faq_vectors``, ``vector_sets``, ``template_overrides`` and
``unmatched_queries``, and writes nothing else. Node writes the account,
billing and support collections, and writes none of these. One database, two
services, no overlapping writers.

Run with:  uvicorn main:app --host 127.0.0.1 --port 8001

Bind to loopback. There is no user-facing authentication here because there
are no users here — if you expose the port, put it behind the same network
boundary as MongoDB.
"""

from __future__ import annotations

import csv
import io
import logging
import os
from typing import Any, Literal, Optional

# ---------------------------------------------------------------------------
# Torch threading — set before anything imports the model
# ---------------------------------------------------------------------------
# This is worth more than it looks. Torch defaults to one thread per core, and
# for a 22M-parameter model embedding one short sentence, the cost of splitting
# that work across cores and joining it back dwarfs the work itself. Measured
# on a 12-core box, one query:
#
#     12 threads (default)  273 ms
#      8 threads             98 ms
#      4 threads             12 ms
#      2 threads             11 ms
#      1 thread              14 ms
#
# A 24x difference, entirely from a setting nobody chose. The default is tuned
# for training a large model, not for answering one question with a small one.
#
# Two is the floor of the flat part of that curve and is also what a small
# cloud instance has, so it is both the fast choice and the honest one. Raise
# it with TORCH_THREADS if a deployment is doing bulk ingest, where batches are
# large enough for the parallelism to pay for itself.
#
# Set here, before `from bot import ...` pulls in sentence-transformers, since
# torch reads this at import.
try:
    import torch

    torch.set_num_threads(int(os.getenv("TORCH_THREADS", "2")))
    # Inter-op parallelism is for running independent graph branches at once.
    # There is one branch here, so extra threads only add scheduling.
    torch.set_num_interop_threads(1)
except (ImportError, RuntimeError):
    # RuntimeError: interop threads can only be set once per process, and a
    # reloader may have done it already. Neither is worth failing startup over.
    pass

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from backend.shared import config, llm
from backend.shared.embeddings import embed_batch, embed_text
from backend.shared.logging_store import count_flagged_inputs, get_gap_summary
from backend.shared.mongo import ensure_indexes

from bot import store
from bot.catalogue import (
    OVERRIDABLE_FIELDS,
    TemplateError,
    get_template,
    is_enabled,
    list_for_admin,
    list_templates,
    reset_override,
    set_override,
)
from bot.graph import BotConfig, build_graph
from bot.ingest import SheetError, ingest_sheet
from bot.readers import SUPPORTED_EXTENSIONS
from bot.templates import (
    OPTIONAL_SHEET_COLUMNS,
    REQUIRED_SHEET_COLUMNS,
    starter_sheet_rows,
    template_public_dict,
)
from bot.widget.package import build_widget_package, build_widget_script, package_filename
from bot.widget.themes import WIDGET_THEMES, get_widget_theme, widget_theme_dict

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("bot-service")

# Sheets are small; anything larger is a mistake worth rejecting before we read
# it into memory. Node enforces the same ceiling before it forwards the upload,
# so this is the second of two checks rather than the only one.
MAX_SHEET_BYTES = 5 * 1024 * 1024

# A shared secret between Node and this service. Not a user credential — it
# only answers "did this come from our own backend". Unset means unenforced,
# which is fine on a laptop where the port is loopback-only and wrong anywhere
# else, so startup says so out loud.
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "").strip()


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    """Reject anything that did not come from the Node backend."""
    if not INTERNAL_API_KEY:
        return
    if x_internal_key != INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is internal to the Nexora backend.",
        )


app = FastAPI(
    title="Nexora AI — bot service",
    description=(
        "Retrieval, sheet ingestion, templates and the embeddable widget. "
        "Internal: reached only by the Node backend, over loopback."
    ),
    version="3.0.0",
    dependencies=[Depends(require_internal_key)],
)


@app.on_event("startup")
def _startup() -> None:
    """
    Apply this service's indexes and warm the embedding model.

    The indexes are not optional: ``bots.user_id`` is unique because one
    account gets one bot, and a missing unique index there is a missing
    constraint rather than a missing optimisation.

    The model is loaded now rather than on first request so the first customer
    query after a deploy does not pay several seconds for it and look broken.
    """
    ensure_indexes()
    logger.info("MongoDB indexes ensured (bot-owned collections).")

    embed_text("warm")
    logger.info("Embedding model %s loaded.", config.EMBEDDING_MODEL)

    if not INTERNAL_API_KEY:
        logger.warning(
            "INTERNAL_API_KEY is not set — this service will answer anyone who "
            "can reach the port. Fine on loopback, not fine anywhere else."
        )


# ---------------------------------------------------------------------------
# Compiled-graph cache
# ---------------------------------------------------------------------------
# Compiling a graph per request would dominate the 25ms budget. Bots change
# rarely, so cache on (bot id, updated_at) and let a template switch or a
# re-upload invalidate the entry naturally.

_GRAPH_CACHE: dict[tuple[str, str], Any] = {}


def _graph_for_bot(bot: dict[str, Any]):
    template = get_template(bot["template_id"])
    if template is None:
        raise HTTPException(
            status_code=500, detail=f"Unknown template {bot['template_id']!r}."
        )

    cache_key = (bot["id"], bot["updated_at"])
    graph = _GRAPH_CACHE.get(cache_key)
    if graph is None:
        graph = build_graph(
            BotConfig(
                collection_name=bot["collection_name"],
                decline_message=template.decline_message,
                near_match_suffix=template.near_match_suffix,
                strong_threshold=template.strong_threshold,
                near_threshold=template.near_threshold,
                log_label=f"bot:{bot['id']}",
                extra_action_patterns=template.extra_action_patterns,
                llm_enabled=bool(bot.get("llm_enabled", 0)),
            )
        )
        # Keep only this bot's newest entry so a busy tenant can't grow the
        # cache without bound by re-uploading.
        for key in [k for k in _GRAPH_CACHE if k[0] == bot["id"]]:
            _GRAPH_CACHE.pop(key, None)
        _GRAPH_CACHE[cache_key] = graph
    return graph


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TemplateResponse(BaseModel):
    id: str
    name: str
    category: str
    icon: str
    tagline: str
    description: str
    scope_label: str
    decline_message: str
    sample_questions: list[str]
    starter_categories: list[str]
    strong_threshold: float
    near_threshold: float
    strictness: str
    # An admin can retire a template. Existing bots keep running on it, so the
    # flag only ever means "still offered to new bots".
    enabled: bool = True


class BotResponse(BaseModel):
    id: str
    name: str
    template_id: str
    template: Optional[TemplateResponse] = None
    status: str
    doc_count: int
    sheet_filename: str
    sheet_uploaded_at: Optional[str] = None
    categories: list[str]
    required_columns: list[str]
    optional_columns: list[str]
    accepted_formats: list[str]
    llm_enabled: bool
    llm_available: bool


class SelectTemplateRequest(BaseModel):
    user_id: str
    template_id: str
    name: str = Field(default="", max_length=80)


class AnsweringModeRequest(BaseModel):
    user_id: str
    enabled: bool


class SheetUploadResponse(BaseModel):
    documents_indexed: int
    skipped_rows: int
    warnings: list[str]
    categories: list[str]
    bot: BotResponse


class Gap(BaseModel):
    question: str
    times_asked: int
    last_asked: str
    best_score: float
    verdict: Literal["nearly", "missing"]


class GapsResponse(BaseModel):
    gaps: list[Gap]
    days: int
    flagged_inputs: int


class AskRequest(BaseModel):
    """
    What Node forwards once it has authenticated and charged for the request.

    ``user_id`` is the API-key account, already resolved. There is no key and
    no cookie here by design — see the module docstring.
    """

    user_id: str
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=128)


class AskResponse(BaseModel):
    response: str
    mode: Literal["strong", "near", "grounded", "decline"]
    matched_question: Optional[str] = None
    confidence: float


class EmbedRequest(BaseModel):
    text: str = Field(default="")


class EmbedBatchRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)


class TemplateUpdateRequest(BaseModel):
    """Every field optional; absent means "leave it alone"."""

    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=80)
    tagline: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)
    scope_label: Optional[str] = Field(default=None, max_length=200)
    decline_message: Optional[str] = Field(default=None, max_length=600)
    near_match_suffix: Optional[str] = Field(default=None, max_length=600)
    strong_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    near_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    reset: list[str] = Field(default_factory=list)


def _bot_public(bot: dict[str, Any]) -> BotResponse:
    template = get_template(bot["template_id"])
    return BotResponse(
        id=bot["id"],
        name=bot["name"],
        template_id=bot["template_id"],
        template=TemplateResponse(**template_public_dict(template)) if template else None,
        status=bot["status"],
        doc_count=bot["doc_count"],
        sheet_filename=bot["sheet_filename"],
        sheet_uploaded_at=bot["sheet_uploaded_at"],
        categories=[c for c in (bot["categories"] or "").split(",") if c],
        required_columns=list(REQUIRED_SHEET_COLUMNS),
        optional_columns=list(OPTIONAL_SHEET_COLUMNS),
        accepted_formats=list(SUPPORTED_EXTENSIONS),
        llm_enabled=bool(bot.get("llm_enabled", 0)),
        # Reported separately so the dashboard can distinguish "you turned this
        # off" from "this is on but Ollama isn't running".
        llm_available=llm.available(),
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    """
    Liveness, and which model is loaded.

    Node compares ``model`` against its own EMBEDDING_MODEL at startup and
    warns loudly on a mismatch, because that is the failure that otherwise
    shows up much later as "search stopped finding anything".
    """
    return {
        "status": "ok",
        "model": config.EMBEDDING_MODEL,
        "llm_available": llm.available(),
    }


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@app.get("/internal/templates", response_model=list[TemplateResponse])
def get_templates() -> list[TemplateResponse]:
    """The ready-made bots a customer can choose from, minus any retired ones."""
    return [TemplateResponse(**t) for t in list_templates()]


@app.get("/internal/templates/{template_id}/starter-sheet")
def download_starter_sheet(template_id: str) -> Response:
    """A pre-filled CSV in the right shape for this template."""
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No such template.")

    buffer = io.StringIO()
    csv.writer(buffer).writerows(starter_sheet_rows(template))

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{template.id}-starter-sheet.csv"'
        },
    )


# ---------------------------------------------------------------------------
# One account's bot
# ---------------------------------------------------------------------------

@app.get("/internal/bot", response_model=BotResponse)
def read_bot(user_id: str) -> BotResponse:
    return _bot_public(store.get_or_create_bot(user_id))


@app.put("/internal/bot", response_model=BotResponse)
def select_template(body: SelectTemplateRequest) -> BotResponse:
    """
    Pick or switch the template.

    Switching keeps the indexed sheet — the template decides scope and wording,
    the sheet decides facts.
    """
    if get_template(body.template_id) is None:
        raise HTTPException(status_code=400, detail="No such template.")

    # A retired template is not offered any more. Bots already on it keep
    # working — this only closes the door to new ones.
    if not is_enabled(body.template_id):
        raise HTTPException(
            status_code=400,
            detail="That template is no longer available. Pick another one.",
        )

    return _bot_public(store.set_template(body.user_id, body.template_id, body.name or None))


@app.put("/internal/bot/answering", response_model=BotResponse)
def set_answering_mode(body: AnsweringModeRequest) -> BotResponse:
    """
    Turn grounded rewording on or off for this bot.

    Refused when no local model is reachable, because silently accepting a
    setting that cannot take effect is how someone ends up believing a feature
    is live when it isn't.
    """
    if body.enabled and not llm.available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No local model is reachable. Start Ollama and set "
                "LLM_ENABLED=true, then try again."
            ),
        )

    return _bot_public(store.set_llm_enabled(body.user_id, body.enabled))


@app.post("/internal/bot/sheet", response_model=SheetUploadResponse)
async def upload_sheet(
    user_id: str,
    file: UploadFile = File(...),
) -> SheetUploadResponse:
    """
    Index the uploaded FAQ, replacing whatever was there.

    Replacing rather than merging is deliberate: the file is the source of
    truth, so a re-upload has to be able to *remove* an answer.
    """
    bot = store.get_or_create_bot(user_id)

    data = await file.read()
    if len(data) > MAX_SHEET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That file is larger than {MAX_SHEET_BYTES // (1024 * 1024)} MB.",
        )

    try:
        result = ingest_sheet(
            collection_name=bot["collection_name"],
            filename=file.filename or "",
            data=data,
        )
    except SheetError as exc:
        # These messages are written for the person who made the sheet.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    updated = store.record_sheet(
        user_id,
        filename=file.filename or "",
        doc_count=result.documents_indexed,
        categories=result.categories,
    )

    return SheetUploadResponse(
        documents_indexed=result.documents_indexed,
        skipped_rows=result.skipped_rows,
        warnings=result.warnings,
        categories=result.categories,
        bot=_bot_public(updated),
    )


@app.get("/internal/bot/gaps", response_model=GapsResponse)
def gaps(user_id: str, days: int = 30) -> GapsResponse:
    """What this account's bot couldn't answer — the rows worth adding."""
    days = max(1, min(days, 365))

    bot = store.get_or_create_bot(user_id)
    label = f"bot:{bot['id']}"

    template = get_template(bot["template_id"])
    near_threshold = template.near_threshold if template else 0.6

    return GapsResponse(
        days=days,
        flagged_inputs=count_flagged_inputs(label, days=days),
        gaps=[
            Gap(
                question=row["question"],
                times_asked=row["times_asked"],
                last_asked=row["last_asked"],
                best_score=round(row["best_score"], 3),
                # Above the near threshold the bot did answer, just hedged —
                # that is a phrasing problem, not a missing answer.
                verdict="nearly" if row["best_score"] >= near_threshold else "missing",
            )
            for row in get_gap_summary(label, days=days)
        ],
    )


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------

@app.post("/internal/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    """
    Answer a question from this account's sheet.

    No credit accounting and no key checking: Node did both before forwarding.
    This endpoint's only job is retrieval, which is the whole reason it is
    written in Python.
    """
    bot = store.get_bot(body.user_id)
    if bot is None or bot["status"] != store.STATUS_READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This account's bot has no indexed FAQ sheet yet. Upload one "
                "from the dashboard before sending questions."
            ),
        )

    result = _graph_for_bot(bot).invoke(
        {"query": body.message, "session_id": body.session_id}
    )
    return AskResponse(
        response=result["response"],
        mode=result["mode"],
        matched_question=result.get("matched_question"),
        confidence=result.get("confidence", 0.0),
    )


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

def _theme_or_404(theme_id: str):
    theme = get_widget_theme(theme_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="Unknown widget theme.")
    return theme


@app.get("/internal/widget/themes")
def list_widget_themes() -> list[dict]:
    """The seven designs. Public through Node — the gallery is browsable."""
    return [widget_theme_dict(theme) for theme in WIDGET_THEMES]


@app.get("/internal/widget/themes/{theme_id}/package")
def download_widget_package(theme_id: str, api_base: str = "") -> Response:
    """
    The install zip for one theme.

    ``api_base`` is passed in by Node rather than derived from this request:
    the origin a customer's website must call is Node's public address, and
    this service only ever sees a loopback one.
    """
    theme = _theme_or_404(theme_id)
    return Response(
        content=build_widget_package(theme, api_base.rstrip("/")),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{package_filename(theme)}"'},
    )


@app.get("/internal/widget/script/{theme_id}")
def hosted_widget_script(theme_id: str, api_base: str = "") -> Response:
    """The same script, for the one-line install with no upload."""
    theme = _theme_or_404(theme_id)
    return Response(
        content=build_widget_script(theme, api_base.rstrip("/")),
        media_type="application/javascript; charset=utf-8",
    )


@app.get("/internal/widget/activation")
def widget_activation(user_id: str) -> dict[str, Any]:
    """
    Whether this account's bot is ready to answer.

    Node has already validated the key; this only reports the bot's state.
    """
    bot = store.get_bot(user_id)
    template = get_template(bot["template_id"]) if bot else None
    return {
        "bot_ready": bool(bot and bot["status"] == store.STATUS_READY),
        "bot_name": bot["name"] if bot else None,
        "template_name": template.name if template else None,
    }


# ---------------------------------------------------------------------------
# Admin — template editing and adoption
# ---------------------------------------------------------------------------

@app.get("/internal/admin/templates")
def admin_templates() -> dict[str, Any]:
    """The catalogue with live values, code defaults, and adoption counts."""
    live = list_for_admin()
    adoption = store.template_adoption()

    for template in live:
        stats = adoption.get(template["id"], {})
        template["bots"] = stats.get("bots", 0)
        template["bots_ready"] = stats.get("ready", 0)
        template["bots_rewording_on"] = stats.get("rewording_on", 0)

    return {"templates": live, "editable_fields": list(OVERRIDABLE_FIELDS)}


@app.patch("/internal/admin/templates/{template_id}")
def update_template(template_id: str, body: TemplateUpdateRequest) -> dict[str, Any]:
    """
    Edit one template. Named fields are set; fields in ``reset`` go back to code.

    Edits reach every bot on the template immediately, including bots already
    running — which is the point, since this exists so a wrong decline message
    can be fixed without a deploy.
    """
    changes: dict[str, Any] = {
        field: value
        for field, value in body.model_dump(exclude={"enabled", "reset"}).items()
        if value is not None
    }
    for field in body.reset:
        if field not in OVERRIDABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"{field} is not an editable field.")
        changes[field] = None

    try:
        updated = set_override(template_id, changes, enabled=body.enabled)
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("Template %s updated: %s", template_id, sorted(changes) or "flags only")
    return updated


@app.post("/internal/admin/templates/{template_id}/reset")
def reset_template(template_id: str) -> dict[str, Any]:
    """Discard every edit to this template and restore the code default."""
    try:
        return reset_override(template_id)
    except TemplateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Owner ops snapshot
# ---------------------------------------------------------------------------

@app.get("/internal/snapshot")
def snapshot(days: int = 7) -> dict[str, Any]:
    """
    Cross-tenant operational state, for the owner assistant.

    The deliberate exception to tenant isolation, and it carries **aggregates
    and question text, never identities** — bots appear as ids and templates,
    not as customer names. Node gates it behind an ``nxo_`` owner key.
    """
    if not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365.")

    from backend.ops import build_snapshot, render_snapshot

    built = build_snapshot(days=days)
    return {"snapshot": built, "rendered": render_snapshot(built)}


# ---------------------------------------------------------------------------
# Account summary, for the dashboard assistant's context block
# ---------------------------------------------------------------------------

@app.get("/internal/account-bot")
def account_bot(user_id: str, days: int = 30, max_gaps: int = 8) -> dict[str, Any]:
    """
    One account's bot and its recent misses, shaped for the assistant prompt.

    Node's assistant builds a context block describing the signed-in customer;
    this is the half of it that lives on the bot side.
    """
    bot = store.get_bot(user_id)
    if bot is None:
        return {"bot": None, "top_unanswered": []}

    template = get_template(bot["template_id"])
    near = template.near_threshold if template else 0.60

    gaps = get_gap_summary(f"bot:{bot['id']}", days=days, limit=max_gaps)

    return {
        "bot": {
            "template": bot["template_id"],
            "template_name": template.name if template else bot["template_id"],
            "status": bot["status"],
            "sheet_rows_indexed": bot["doc_count"],
            "sheet_filename": bot["sheet_filename"],
            "grounded_rewording": bool(bot.get("llm_enabled", 0)),
            "strong_threshold": template.strong_threshold if template else None,
            "near_threshold": template.near_threshold if template else None,
        },
        "top_unanswered": [
            {
                "question": gap["question"],
                "times_asked": gap["times_asked"],
                "best_score": round(gap["best_score"], 3),
                "verdict": "needs phrasing" if gap["best_score"] >= near else "not covered",
            }
            for gap in gaps
        ],
    }


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
# Kept as endpoints in their own right. Nothing in Node needs them today —
# retrieval and ingest both happen here now — but they are the one piece of
# this service with no Python-specific reason to stay internal, and exposing
# them costs nothing.

@app.post("/embed")
def embed(request: EmbedRequest) -> dict[str, list[float]]:
    return {"embedding": embed_text(request.text)}


@app.post("/embed-batch")
def embed_batch_endpoint(request: EmbedBatchRequest) -> dict[str, list[list[float]]]:
    if not request.texts:
        return {"embeddings": []}
    return {"embeddings": embed_batch(request.texts)}
