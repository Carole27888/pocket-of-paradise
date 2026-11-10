from django import template
from django.utils import timezone
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def waited(since, warn_after=30):
    """'12 min' / '2h 05m' since a timestamp; red once it passes `warn_after` minutes."""
    if not since:
        return ""
    mins = max(0, int((timezone.now() - since).total_seconds() // 60))
    if mins < 60:
        label = f"{mins} min"
    elif mins < 60 * 24:
        label = f"{mins // 60}h {mins % 60:02d}m"
    else:
        label = f"{mins // 1440}d {mins % 1440 // 60}h"
    return format_html('<span class="waited{}">{}</span>', " long" if mins >= warn_after else "", label)


@register.simple_tag
def queue_pill(enquiry):
    from ..services import queue_state

    state = queue_state(enquiry)
    label, tone = {"waiting": ("Waiting", "danger"), "assigned": ("Assigned", "warning"),
                   "contacted": ("Contacted", "success")}[state]
    return format_html('<span class="pill pill-{}">{}</span>', tone, label)
