"""Each module gets its own "User Manual" and "Calendar" menu entries that keep the sidebar in that module
(the same pages open in the module you came from)."""
from django.urls import path

from apps.accounts.permissions import module_required

from .keys import CALENDAR_KEYS, MANUAL_KEYS

def _manual(key, slug):
    @module_required(key)
    def view(request):
        from .views import manual

        return manual(request, slug)

    return view


def _calendar(key):
    @module_required(key)
    def view(request):
        from apps.schedule.views import calendar_view

        return calendar_view(request)

    return view


def urlpatterns_for_modules():
    out = []
    for key, slug in MANUAL_KEYS.items():
        out.append(path(f"{key}/manual/", _manual(key, slug), name=f"manual_{key}"))
    for key in CALENDAR_KEYS:
        out.append(path(f"{key}/calendar/", _calendar(key), name=f"calendar_{key}"))
    return out
