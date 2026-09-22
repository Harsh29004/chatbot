"""
Where the browser reports its own crashes.

Firebase Crashlytics has no Web SDK, so the page does the catching and this is
where the stack trace lands. The Analytics ``exception`` event fires from the
browser in parallel and gives the Firebase console a rate to alarm on; this
endpoint gives a human something to read when that alarm goes off.

Unauthenticated on purpose
--------------------------
The crashes worth hearing about most are the ones that happen *before* sign-in
— a broken bundle, a failing auth redirect, a page that throws on first paint.
Requiring a session would filter out exactly those.

So it is treated as a public write endpoint, which means:

* **Rate limited per IP**, in MongoDB, so the limit survives a restart and is
  shared across workers. A page stuck in a render-crash-remount loop can emit
  hundreds of reports a second, and that is a self-inflicted flood before it
  is ever an attack.
* **Every field length-capped** on the way in (``telemetry_store``).
* **Always answers 202**, whatever happened. A reporter that returns errors
  invites a retry loop on top of the crash loop it is already reporting, and
  tells a prober whether its input was interesting.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Request
from pydantic import BaseModel, Field

from backend.billing import db
from backend.shared import config, rate_limits
from backend.shared import telemetry_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/telemetry", tags=["Telemetry"])

SESSION_COOKIE = "nexora_session"

_ERROR_BUCKET = "client_error"
_ERROR_WINDOW_SECONDS = 3600


class ClientErrorRequest(BaseModel):
    """
    One crash, as the browser saw it.

    The limits here are the first line of defence and are set well above what
    a real report needs — the store clips again on write. Pydantic rejecting
    an oversized field costs nothing; a 2 MB stack reaching MongoDB does.
    """

    message: str = Field(default="", max_length=2000)
    stack: str = Field(default="", max_length=20000)
    # "error" | "unhandledrejection" | "react" | "api" — free-form, since the
    # page may grow new sources and an unknown one should still be recorded.
    kind: str = Field(default="error", max_length=40)
    fingerprint: str = Field(default="", max_length=64)
    route: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=1000)
    release: str = Field(default="", max_length=64)
    component_stack: str = Field(default="", max_length=10000)
    # Whether the user was left looking at a broken page, as opposed to an
    # error that was caught and recovered from. Mirrors the GA4 flag.
    fatal: bool = False
    context: Optional[dict[str, Any]] = None


def _client_ip(request: Request) -> str:
    """
    The caller's address, trusting the proxy's first hop only.

    Same approach as the signup limiter: behind Caddy the socket address is
    the proxy, so ``X-Forwarded-For`` is what identifies the browser. Only the
    leftmost entry is used, and it is bounded, because the rest of that header
    is whatever the client chose to send.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


@router.post("/error", status_code=202, include_in_schema=False)
async def report_error(
    body: ClientErrorRequest,
    request: Request,
    nexora_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, str]:
    """
    Record a client-side crash.

    Answers 202 in every case, including when the report was dropped. The
    browser has nothing useful to do with a failure here, and a page that is
    already crashing should not also be handling errors from its crash
    reporter.
    """
    if not config.TELEMETRY_ENABLED:
        return {"status": "disabled"}

    ip = _client_ip(request)

    try:
        seen = rate_limits.count(_ERROR_BUCKET, ip, _ERROR_WINDOW_SECONDS)
        if seen >= config.TELEMETRY_MAX_ERRORS_PER_IP_PER_HOUR:
            # Logged once per over-limit report at debug, not warning: a
            # crash loop would otherwise move the flood from MongoDB to the
            # log file, which is not an improvement.
            logger.debug("Client error report rate limited for %s", ip)
            return {"status": "throttled"}
        rate_limits.record(_ERROR_BUCKET, ip, _ERROR_WINDOW_SECONDS)
    except Exception:  # noqa: BLE001 - a limiter outage must not lose reports
        logger.debug("Telemetry rate limiter unavailable; accepting report")

    # Attributing the crash to an account when there is one. A failure to
    # resolve the session is not interesting — the report is still worth
    # keeping without a name on it.
    customer_id: str | None = None
    if nexora_session:
        try:
            customer = db.get_session_customer(nexora_session)
            if customer:
                customer_id = str(customer["id"])
        except Exception:  # noqa: BLE001
            customer_id = None

    telemetry_store.record_error(
        message=body.message,
        stack=body.stack,
        kind=body.kind,
        fingerprint=body.fingerprint,
        route=body.route,
        url=body.url,
        user_agent=request.headers.get("user-agent", ""),
        release=body.release,
        component_stack=body.component_stack,
        fatal=body.fatal,
        customer_id=customer_id,
        client_ip=ip,
        context=body.context,
    )

    return {"status": "recorded"}
