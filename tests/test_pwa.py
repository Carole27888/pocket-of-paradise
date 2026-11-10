import json
from pathlib import Path

import pytest
from django.conf import settings

from apps.pwa.devices import ICON_SIZES, MASKABLE_SIZES, splash_images

STATIC = Path(settings.BASE_DIR) / "static"


def _file_for(url):
    return STATIC / url.split(settings.STATIC_URL, 1)[1]


@pytest.mark.django_db
def test_manifest_fields_and_files(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("application/manifest+json")
    m = json.loads(r.content)
    for key in ("name", "short_name", "start_url", "scope", "display", "theme_color", "background_color", "icons",
                "shortcuts", "screenshots", "id"):
        assert key in m, key
    assert m["display"] == "standalone"
    assert m["theme_color"] == "#1F6F6B"
    sizes = {i["sizes"] for i in m["icons"] if i["purpose"] == "any"}
    assert {"192x192", "512x512"} <= sizes
    assert any(i["purpose"] == "maskable" and i["sizes"] == "512x512" for i in m["icons"])
    assert {s["form_factor"] for s in m["screenshots"]} == {"wide", "narrow"}
    assert [s["name"] for s in m["shortcuts"]] == ["New Enquiry", "Today's Departures", "Clock in / out", "Sales Pipeline"]
    for item in m["icons"] + m["screenshots"]:
        assert _file_for(item["src"]).exists(), item["src"]


def test_icon_and_splash_files_exist():
    for s in ICON_SIZES:
        assert (STATIC / f"pwa/icon-{s}.png").exists()
    for s in MASKABLE_SIZES:
        assert (STATIC / f"pwa/maskable-{s}.png").exists()
    for f in ("apple-touch-icon.png", "favicon.ico", "favicon-32.png"):
        assert (STATIC / "pwa" / f).exists()
    splashes = list(splash_images())
    assert len(splashes) >= 32  # 16 devices x portrait + landscape
    for s in splashes:
        assert (STATIC / s["file"]).exists(), s["file"]


@pytest.mark.django_db
def test_service_worker_served_from_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r["Content-Type"].startswith("application/javascript")
    assert r["Service-Worker-Allowed"] == "/"
    assert "no-cache" in r["Cache-Control"]
    body = r.content.decode()
    assert settings.PWA_VERSION in body
    assert "/offline/" in body
    assert "NEVER" in body and "api" in body  # never-cache list


@pytest.mark.django_db
def test_offline_page_renders_without_login(client):
    r = client.get("/offline/")
    assert r.status_code == 200
    assert b"You are offline" in r.content
    assert b"Retry" in r.content


@pytest.mark.django_db
def test_head_includes_pwa_tags(admin_client):
    html = admin_client.get("/").content.decode()
    assert 'rel="manifest"' in html
    assert 'name="theme-color"' in html
    assert "apple-touch-startup-image" in html
    assert 'viewport-fit=cover' in html
    assert "black-translucent" in html
