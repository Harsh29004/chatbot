"""
Widget themes, install packages, hosted scripts, and key activation.

Everything a customer needs to put their bot on their own site, in one of
seven designs, without writing a chat UI.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.shared.api_keys import validate_api_key
from bot import store
from bot.catalogue import get_template
from bot.widget.package import (
    build_widget_package,
    build_widget_script,
    package_filename,
)
from bot.widget.themes import WIDGET_THEMES, get_widget_theme, widget_theme_dict

router = APIRouter(tags=["Widget"])

# Behind a reverse proxy the request's own base URL can be the internal
# address; PUBLIC_API_ORIGIN pins the one customers' sites should call.
PUBLIC_API_ORIGIN = os.getenv("PUBLIC_API_ORIGIN", "").strip().rstrip("/")


def _api_base(request: Request) -> str:
    return PUBLIC_API_ORIGIN or str(request.base_url).rstrip("/")


def _theme_or_404(theme_id: str):
    theme = get_widget_theme(theme_id)
    if theme is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown widget theme.")
    return theme


class ActivationResponse(BaseModel):
    active: bool
    bot_ready: bool
    bot_name: str | None = None
    template_name: str | None = None


@router.get("/api/widget/themes")
async def list_widget_themes() -> list[dict]:
    """The seven designs. Public — the gallery is browsable before sign-up."""
    return [widget_theme_dict(theme) for theme in WIDGET_THEMES]


@router.get("/api/widget/themes/{theme_id}/package")
async def download_widget_package(theme_id: str, request: Request) -> Response:
    """
    The install zip for one theme.

    Public on purpose: it contains no key and no account data, only the design
    and the API origin. The key is what activates it.
    """
    theme = _theme_or_404(theme_id)
    return Response(
        content=build_widget_package(theme, _api_base(request)),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{package_filename(theme)}"'},
    )


@router.get("/widget/v1/{theme_id}.js", include_in_schema=False)
async def hosted_widget_script(theme_id: str, request: Request) -> Response:
    """The same script, served from here for a one-line install with no upload."""
    theme = _theme_or_404(theme_id)
    return Response(
        content=build_widget_script(theme, _api_base(request)),
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/v1/widget/activate", response_model=ActivationResponse, tags=["Bot API"])
async def activate_widget(x_api_key: str = Header(..., alias="X-Api-Key")) -> ActivationResponse:
    """
    Check a key from an installed widget. Costs no credits.

    Deliberately does not go through ``verify_api_key``: that dependency
    charges by message length, and loading a page is not asking a question.
    """
    key_record = validate_api_key(x_api_key)
    if key_record is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or revoked API key.")

    bot = store.get_bot(key_record["user_id"])
    template = get_template(bot["template_id"]) if bot else None
    return ActivationResponse(
        active=True,
        bot_ready=bool(bot and bot["status"] == store.STATUS_READY),
        bot_name=bot["name"] if bot else None,
        template_name=template.name if template else None,
    )
