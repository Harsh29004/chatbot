"""
Nexora AI — main FastAPI application.

Mounts the platform API (accounts, billing, dashboard), the bot API
(templates, sheet upload, ``/v1/ask``), API-key management, and a health
check.

Run with:  ``uvicorn server:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.admin.router import router as admin_router
from backend.api_keys_router import router as keys_router
from backend.billing import entitlements
from backend.billing.db import purge_dead_sessions
from backend.assistant.router import router as assistant_router
from backend.billing.router import router as platform_router
from backend.owner_router import router as owner_router
from backend.support.router import router as support_router
from bot.router import router as bot_router
from bot.widget.router import router as widget_router
from backend.shared.mongo import ensure_indexes
from backend.shared.schemas import HealthResponse

logger = logging.getLogger(__name__)


# How often to retire lapsed subscriptions. The dashboard also syncs on read,
# but nobody should keep a paid allowance just because they stopped logging in.
ENTITLEMENT_SWEEP_SECONDS = int(os.getenv("ENTITLEMENT_SWEEP_SECONDS", "900"))


def _sweep_once() -> tuple[int, int]:
    """Withdraw lapsed entitlements and clear out dead sessions."""
    return entitlements.sync(), purge_dead_sessions()


async def _background_sweep() -> None:
    """Periodically expire lapsed plans and tidy the sessions table."""
    while True:
        await asyncio.sleep(ENTITLEMENT_SWEEP_SECONDS)
        try:
            withdrawn, purged = await asyncio.to_thread(_sweep_once)
            if withdrawn or purged:
                logger.info(
                    "Sweep: withdrew %d account(s), purged %d dead session(s).",
                    withdrawn, purged,
                )
        except Exception:
            # A failed sweep must never take the server down with it; the next
            # tick retries, and the dashboard syncs on read regardless.
            logger.exception("Background sweep failed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup tasks:
    1. Apply the MongoDB indexes every store declares
    2. Retire any subscriptions that lapsed while the server was down
    3. Start the periodic entitlement sweep
    """
    # MongoDB builds collections on first write, so there is no schema step —
    # but the indexes are not optional. Two of them (one account per mailbox,
    # one referral payout per invoice) are constraints the application relies
    # on rather than optimisations, so a failure here must stop startup rather
    # than leave the server running without them.
    ensure_indexes()
    logger.info("MongoDB indexes ensured.")

    withdrawn, purged = _sweep_once()
    if withdrawn or purged:
        logger.info(
            "Startup sweep: withdrew %d account(s), purged %d dead session(s).",
            withdrawn, purged,
        )

    sweep = asyncio.create_task(_background_sweep())
    try:
        yield
    finally:
        sweep.cancel()


app = FastAPI(
    title="Nexora AI",
    description=(
        "Retrieval-grounded FAQ chatbots, sold as a service. Customers pick a "
        "template, upload their FAQ sheet, and get an API key. Answers come "
        "from their own sheet. A bot answers verbatim by default; an owner "
        "may opt in to grounded rewording, where a local model may change "
        "the words but never the facts."
    ),
    version="3.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# The dashboard authenticates with a session cookie, and browsers refuse to
# send credentials to a wildcard origin — so the web origins are listed
# explicitly. Set WEB_ORIGINS (comma-separated) in production.
WEB_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "WEB_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

CREDIT_HEADERS = [
    "X-Credits-Remaining",
    "X-Credits-Daily-Limit",
    "X-Credits-Reset-At",
    "X-Credit-Cost",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=CREDIT_HEADERS,
)


class PublicApiCors:
    """
    Open CORS for the key-authenticated ``/v1`` API only.

    An installed widget calls ``/v1`` from the customer's own domain, which
    can't be listed in WEB_ORIGINS ahead of time. That is safe to allow from
    any origin because these routes authenticate with the ``X-Api-Key``
    header, never the session cookie — credentials are not allowed here, so
    a hostile page gains nothing it didn't already have. Every other path
    falls through to the strict, cookie-aware policy above.

    Added after ``CORSMiddleware`` so it sits outside it and answers first.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/"):
            await self.app(scope, receive, send)
            return

        cors = [
            (b"access-control-allow-origin", b"*"),
            (b"access-control-expose-headers", ", ".join(CREDIT_HEADERS).encode()),
        ]

        if scope["method"] == "OPTIONS":
            await send({
                "type": "http.response.start",
                "status": 204,
                "headers": cors + [
                    (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                    (b"access-control-allow-headers", b"Content-Type, X-Api-Key"),
                    (b"access-control-max-age", b"600"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message):
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if not name.lower().startswith(b"access-control-")
                ]
                message = {**message, "headers": headers + cors}
            await send(message)

        await self.app(scope, receive, send_with_cors)


app.add_middleware(PublicApiCors)


# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(platform_router)
app.include_router(bot_router)
app.include_router(widget_router)
app.include_router(keys_router)
app.include_router(owner_router)
app.include_router(assistant_router)
app.include_router(support_router)
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health() -> HealthResponse:
    """Basic health check."""
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# Static web app (production only)
# ---------------------------------------------------------------------------
# In development Vite serves the SPA on :5173 and proxies /api here. In a
# container the built bundle is copied to frontend/dist and served from this same
# origin, which is why the session cookie needs no cross-site handling in
# production. Registered last so it can never shadow an API route.

WEB_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if WEB_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=WEB_DIST / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """
        Serve the SPA shell for any non-API path.

        Client-side routes like /dashboard are not files on disk, so anything
        that isn't a real asset gets index.html and React Router takes over.
        """
        candidate = (WEB_DIST / full_path).resolve()
        # Only serve real files that are genuinely inside the bundle — without
        # the containment check, a crafted path could escape web/dist.
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(WEB_DIST)
        ):
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")

    logger.info("Serving the web app from %s", WEB_DIST)
else:
    logger.info("No frontend/dist bundle found — API only (use the Vite dev server).")
