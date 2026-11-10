import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

from .devices import ICON_SIZES, MASKABLE_SIZES


def manifest_data():
    icons = [{"src": static(f"pwa/icon-{s}.png"), "sizes": f"{s}x{s}", "type": "image/png", "purpose": "any"}
             for s in ICON_SIZES]
    icons += [{"src": static(f"pwa/maskable-{s}.png"), "sizes": f"{s}x{s}", "type": "image/png",
               "purpose": "maskable"} for s in MASKABLE_SIZES]
    sc_icon = [{"src": static("pwa/icon-96.png"), "sizes": "96x96", "type": "image/png"}]
    return {
        "name": "Pocket of Paradise Travel Company",
        "short_name": "Paradise",
        "description": "Tours, bookings, fleet and finance for Pocket of Paradise Travel Company. Travel Company.",
        "id": "/",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "display_override": ["window-controls-overlay", "standalone"],
        "orientation": "any",
        "theme_color": settings.PWA_THEME_COLOR,
        "background_color": settings.PWA_BACKGROUND_COLOR,
        "lang": "en-GB",
        "categories": ["business", "travel", "productivity"],
        "icons": icons,
        "shortcuts": [
            {"name": "New Enquiry", "short_name": "Enquiry", "url": "/enquiries/new/?source=pwa", "icons": sc_icon},
            {"name": "Today's Departures", "short_name": "Departures", "url": "/trips/board/?source=pwa", "icons": sc_icon},
            {"name": "Clock in / out", "short_name": "Clock", "url": "/hr/attendance/me/?source=pwa", "icons": sc_icon},
            {"name": "Sales Pipeline", "short_name": "Pipeline", "url": "/sales/pipeline/?source=pwa", "icons": sc_icon},
        ],
        "screenshots": [
            {"src": static("pwa/screenshot-wide.png"), "sizes": "1280x800", "type": "image/png",
             "form_factor": "wide", "label": "Operations dashboard on desktop"},
            {"src": static("pwa/screenshot-narrow.png"), "sizes": "750x1334", "type": "image/png",
             "form_factor": "narrow", "label": "Enquiries on a phone"},
        ],
    }


def manifest(request):
    resp = HttpResponse(json.dumps(manifest_data(), indent=2), content_type="application/manifest+json")
    resp["Cache-Control"] = "no-cache"
    return resp


PRECACHE = [
    "css/tokens.css", "css/base.css", "css/layout.css", "css/components.css", "css/utilities.css", "css/vibrant.css",
    "js/app.js", "js/charts.js", "js/sales.js", "js/offline-checkin.js",
    "vendor/htmx/htmx.min.js", "vendor/alpine/alpine.min.js", "vendor/chartjs/chart.umd.min.js",
    "fonts/manrope-latin-400-normal.woff2", "fonts/manrope-latin-600-normal.woff2",
    "fonts/manrope-latin-700-normal.woff2", "fonts/manrope-latin-800-normal.woff2",
    "fonts/fraunces-latin-700-normal.woff2",
    "pwa/icon-192.png", "pwa/icon-512.png", "brand/logo-full.png", "pwa/mark-96.png",
]


def service_worker(request):
    ctx = {
        "version": settings.PWA_VERSION,
        "precache": json.dumps(["/offline/"] + [static(p) for p in PRECACHE]),
        "static_prefix": settings.STATIC_URL,
        "debug": settings.DEBUG,
    }
    resp = render(request, "pwa/sw.js", ctx, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


@cache_control(no_cache=True)
def offline(request):
    return render(request, "pwa/offline.html")


def health(request):
    return JsonResponse({"ok": True, "version": settings.PWA_VERSION})
