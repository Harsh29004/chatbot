"""
Tests for the installable widget: the seven designs, the package a customer
downloads, key activation, and the CORS that lets a customer's own domain
reach ``/v1``.
"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.shared import api_keys as ak
from bot.widget.package import build_widget_package, build_widget_script
from bot.widget.themes import WIDGET_THEMES, get_widget_theme
from tests.conftest import DEMO_EMAIL


@pytest.fixture()
def client() -> TestClient:
    from backend.server import app

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

def test_there_are_seven_themes_with_unique_ids():
    ids = [theme.id for theme in WIDGET_THEMES]
    assert len(ids) == 7
    assert len(set(ids)) == 7


def test_themes_offer_both_layouts():
    assert {theme.layout for theme in WIDGET_THEMES} == {"bubble", "drawer"}


def test_themes_endpoint_is_public(client):
    response = client.get("/api/widget/themes")
    assert response.status_code == 200
    assert [t["id"] for t in response.json()] == [t.id for t in WIDGET_THEMES]


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------

def test_script_has_theme_and_api_base_baked_in():
    theme = get_widget_theme("midnight")
    script = build_widget_script(theme, "https://api.example.test")

    assert "/*__NEXORA_THEME__*/" not in script
    assert "/*__NEXORA_API_BASE__*/" not in script
    assert '"https://api.example.test"' in script
    assert theme.primary in script


def test_package_contains_install_files_and_no_key():
    theme = get_widget_theme("aurora")
    archive = zipfile.ZipFile(io.BytesIO(build_widget_package(theme, "https://api.example.test")))
    names = set(archive.namelist())

    folder = "nexora-widget-aurora"
    for expected in (
        "nexora-widget.js",
        "index.html",
        "README.md",
        "nexora.config.json",
        "server-proxy/node-express.js",
        "server-proxy/python-fastapi.py",
        "server-proxy/php-proxy.php",
    ):
        assert f"{folder}/{expected}" in names

    config = json.loads(archive.read(f"{folder}/nexora.config.json"))
    assert config["theme"]["id"] == "aurora"

    # Placeholders like "nxk_…" are fine; anything shaped like a real key is not.
    everything = b"".join(archive.read(name) for name in names)
    assert re.search(rb"nx[ko]_[A-Za-z0-9_-]{16,}", everything) is None


def test_package_download(client):
    response = client.get("/api/widget/themes/harbor/package")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "nexora-widget-harbor" in response.headers["content-disposition"]


def test_unknown_theme_is_404(client):
    assert client.get("/api/widget/themes/nope/package").status_code == 404
    assert client.get("/widget/v1/nope.js").status_code == 404


def test_hosted_script(client):
    response = client.get("/widget/v1/sunset.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "NexoraWidget" in response.text


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def test_activation_rejects_a_bad_key(client):
    response = client.get("/v1/widget/activate", headers={"X-Api-Key": "nxk_garbage"})
    assert response.status_code == 403


def test_activation_reports_a_ready_bot_and_costs_nothing(client, demo_bot):
    issued = ak.generate_api_key(owner_email=DEMO_EMAIL, label="widget")
    user_id = demo_bot["user_id"]
    before = ak.get_credits_used_today(user_id)

    response = client.get("/v1/widget/activate", headers={"X-Api-Key": issued["api_key"]})

    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["bot_ready"] is True
    assert ak.get_credits_used_today(user_id) == before


def test_activation_with_no_sheet_is_not_ready(client):
    issued = ak.generate_api_key(owner_email="fresh@example.test", label="widget")
    response = client.get("/v1/widget/activate", headers={"X-Api-Key": issued["api_key"]})
    assert response.status_code == 200
    assert response.json()["bot_ready"] is False


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def test_v1_preflight_allows_any_origin_without_credentials(client):
    response = client.options(
        "/v1/ask",
        headers={
            "Origin": "https://customer-shop.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"
    assert "x-api-key" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers


def test_v1_responses_carry_open_cors(client):
    response = client.get(
        "/v1/widget/activate",
        headers={"Origin": "https://customer-shop.example", "X-Api-Key": "nxk_garbage"},
    )
    assert response.headers["access-control-allow-origin"] == "*"


def test_dashboard_api_keeps_strict_cors(client):
    response = client.get(
        "/api/widget/themes",
        headers={"Origin": "https://evil.example"},
    )
    assert response.headers.get("access-control-allow-origin") != "*"


def test_script_is_pure_ascii_so_any_server_charset_works():
    # The Sunset greeting has an emoji; it must survive as an escape.
    script = build_widget_script(get_widget_theme("sunset"), "https://api.example.test")
    assert script.isascii()
    assert r"\ud83d\ude0a" in script
