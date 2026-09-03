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

from apps.api_keys_router import router as keys_router
from apps.billing import entitlements
from apps.billing.db import init_billing_tables, purge_dead_sessions
from apps.billing.router import router as platform_router
from apps.bot_engine.store import init_bot_tables
from apps.bots_router import router as bots_router
from shared.api_keys import init_api_key_tables
from shared.logging_store import init_db
from shared.schemas import HealthResponse

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
    1. Initialise SQLite tables (logging + API keys + billing + bots)
    2. Retire any subscriptions that lapsed while the server was down
    3. Start the periodic entitlement sweep
    """
    init_db()
    init_api_key_tables()
    init_billing_tables()
    init_bot_tables()
    logger.info("Database tables initialised.")

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
        "from their own sheet — there is no generation step, so the bot "
        "cannot invent one."
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Credits-Remaining",
        "X-Credits-Daily-Limit",
        "X-Credits-Reset-At",
        "X-Credit-Cost",
    ],
)


# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(platform_router)
app.include_router(bots_router)
app.include_router(keys_router)


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
# container the built bundle is copied to web/dist and served from this same
# origin, which is why the session cookie needs no cross-site handling in
# production. Registered last so it can never shadow an API route.

WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"

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
    logger.info("No web/dist bundle found — API only (use the Vite dev server).")
