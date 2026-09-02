"""
Template picking, sheet upload, and the public ask endpoint.

The shape a customer experiences is: choose a template, upload your sheet,
copy your key, call ``POST /v1/ask``. Everything here serves one of those
four steps.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from shared.api_keys import get_user_by_email
from shared.auth import verify_api_key

from apps.billing.router import current_customer
from apps.bot_engine import store
from apps.bot_engine.graph import BotConfig, build_graph
from apps.bot_engine.ingest import SheetError, ingest_sheet
from apps.bot_engine.templates import (
    OPTIONAL_SHEET_COLUMNS,
    REQUIRED_SHEET_COLUMNS,
    get_template,
    list_templates,
    starter_sheet_rows,
    template_public_dict,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Bots"])

# Sheets are small; anything larger is a mistake worth rejecting before we
# read it into memory.
MAX_SHEET_BYTES = 5 * 1024 * 1024  # 5 MB


# ---------------------------------------------------------------------------
# Compiled-graph cache
# ---------------------------------------------------------------------------
# Compiling a LangGraph per request would dominate the 25ms budget. Bots
# change rarely, so cache on (bot id, updated_at) and let a template switch or
# a re-upload invalidate the entry naturally.

_GRAPH_CACHE: dict[tuple[int, str], Any] = {}


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


class BotResponse(BaseModel):
    id: int
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


class SelectTemplateRequest(BaseModel):
    template_id: str
    name: str = Field(default="", max_length=80)


class SheetUploadResponse(BaseModel):
    documents_indexed: int
    skipped_rows: int
    warnings: list[str]
    categories: list[str]
    bot: BotResponse


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(..., min_length=1, max_length=128)


class AskResponse(BaseModel):
    response: str
    mode: Literal["strong", "near", "decline"]
    matched_question: Optional[str] = None
    confidence: float


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
    )


def _account_user_id(customer: dict[str, Any]) -> int:
    """
    Map a signed-in customer to their API-key account.

    The two are linked by email: billing knows the person, ``shared.api_keys``
    knows the account their keys and credits hang off.
    """
    user = get_user_by_email(customer["email"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No API account yet — create an API key first.",
        )
    return user["id"]


# ---------------------------------------------------------------------------
# Templates (public — the picker is on the marketing site too)
# ---------------------------------------------------------------------------

@router.get("/api/templates", response_model=list[TemplateResponse])
async def get_templates() -> list[TemplateResponse]:
    """The ten ready-made bots a customer can choose from."""
    return [TemplateResponse(**t) for t in list_templates()]


@router.get("/api/templates/{template_id}/starter-sheet")
async def download_starter_sheet(template_id: str) -> Response:
    """
    A pre-filled CSV in the right shape for this template.

    Handing someone a correctly-shaped sheet with worked examples removes the
    single biggest reason setup stalls.
    """
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No such template.")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(starter_sheet_rows(template))

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{template.id}-starter-sheet.csv"'
            )
        },
    )


# ---------------------------------------------------------------------------
# The customer's bot (dashboard, session cookie)
# ---------------------------------------------------------------------------

@router.get("/api/bot", response_model=BotResponse)
async def read_bot(customer: dict = Depends(current_customer)) -> BotResponse:
    user_id = _account_user_id(customer)
    return _bot_public(store.get_or_create_bot(user_id))


@router.put("/api/bot", response_model=BotResponse)
async def select_template(
    body: SelectTemplateRequest,
    customer: dict = Depends(current_customer),
) -> BotResponse:
    """
    Pick or switch the template.

    Switching keeps the indexed sheet — the template decides scope and
    wording, the sheet decides facts.
    """
    if get_template(body.template_id) is None:
        raise HTTPException(status_code=400, detail="No such template.")

    user_id = _account_user_id(customer)
    bot = store.set_template(user_id, body.template_id, body.name or None)
    return _bot_public(bot)


@router.post("/api/bot/sheet", response_model=SheetUploadResponse)
async def upload_sheet(
    file: UploadFile = File(...),
    customer: dict = Depends(current_customer),
) -> SheetUploadResponse:
    """
    Upload the FAQ sheet. This replaces whatever was indexed before.

    Replacing rather than merging is deliberate: the sheet is the source of
    truth, so a re-upload has to be able to *remove* an answer.
    """
    user_id = _account_user_id(customer)
    bot = store.get_or_create_bot(user_id)

    data = await file.read()
    if len(data) > MAX_SHEET_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Sheet is larger than {MAX_SHEET_BYTES // (1024 * 1024)} MB.",
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


@router.post("/api/bot/preview", response_model=AskResponse)
async def preview(
    body: AskRequest,
    customer: dict = Depends(current_customer),
) -> AskResponse:
    """
    Try a question from the dashboard without spending credits.

    Testing your own bot shouldn't cost you anything — that is exactly the
    activity we want people doing before they go live.
    """
    user_id = _account_user_id(customer)
    bot = store.get_or_create_bot(user_id)

    if bot["status"] != store.STATUS_READY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Upload your FAQ sheet first — the bot has nothing to answer from.",
        )

    result = _graph_for_bot(bot).invoke(
        {"query": body.message, "session_id": f"preview:{customer['id']}"}
    )
    return AskResponse(
        response=result["response"],
        mode=result["mode"],
        matched_question=result.get("matched_question"),
        confidence=result.get("confidence", 0.0),
    )


# ---------------------------------------------------------------------------
# The public endpoint customers integrate against
# ---------------------------------------------------------------------------

@router.post("/v1/ask", response_model=AskResponse, tags=["Bot API"])
async def ask(
    body: AskRequest,
    key_record: dict = Depends(verify_api_key),
) -> AskResponse:
    """
    Ask this account's bot a question.

    Requires ``X-Api-Key``. Costs credits by message length (see
    ``GET /api/keys/pricing``). The answer comes verbatim from the account's
    own sheet, or the template's decline message — never anything invented.
    """
    bot = store.get_bot(key_record["user_id"])
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
