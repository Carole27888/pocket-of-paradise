"""Portable JSON backup. Separate from any server-side disaster recovery: this one is a plain file ICT downloads and keeps elsewhere.

A new file is prepared only when due (frequency chosen by ICT, checked daily). ICT is notified; the first download closes the row.
Secrets stay out: saved integration keys, sessions and earlier backup rows are never dumped."""
from __future__ import annotations

import json
from datetime import timedelta
from io import StringIO

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.utils import timezone

from .models import JsonBackupRun, JsonBackupSettings

EXCLUDE = ["contenttypes", "auth.permission", "sessions", "ict.jsonbackuprun", "system.integrationcredential", "admin.logentry"]
DUE_AFTER = {"daily": timedelta(hours=20), "weekly": timedelta(days=6, hours=12), "monthly": timedelta(days=27)}


def period_label(frequency: str, when=None) -> str:
    when = when or timezone.localdate()
    if frequency == "weekly":
        return f"Week of {when - timedelta(days=when.weekday()):%-d %b %Y}"
    if frequency == "monthly":
        return f"{when:%B %Y}"
    return f"{when:%-d %b %Y}"


def is_due(now=None) -> bool:
    now = now or timezone.now()
    last = JsonBackupRun.objects.order_by("-created_at").first()
    return last is None or now - last.created_at >= DUE_AFTER[JsonBackupSettings.get().frequency]


def create_backup() -> JsonBackupRun:
    cfg = JsonBackupSettings.get()
    buf = StringIO()
    args = ["dumpdata", "--natural-foreign", "--natural-primary", "--indent", "0"]
    for e in EXCLUDE:
        args += ["--exclude", e]
    call_command(*args, stdout=buf)
    raw = buf.getvalue()
    count = len(json.loads(raw or "[]"))
    run = JsonBackupRun(period_label=period_label(cfg.frequency), object_count=count)
    run.file.save(f"backup-{timezone.localtime():%Y%m%d-%H%M%S}.json", ContentFile(raw.encode("utf-8")), save=False)
    run.size = run.file.size
    run.save()
    _prune(cfg.keep)
    _notify(run)
    return run


def _prune(keep: int):
    for old in list(JsonBackupRun.objects.order_by("-created_at")[max(keep, 1):]):
        old.file.delete(save=False)
        old.delete()


def mark_downloaded(run: JsonBackupRun, user):
    if run.is_pending:
        run.downloaded_at, run.downloaded_by = timezone.now(), user
        run.save(update_fields=["downloaded_at", "downloaded_by"])


def _notify(run: JsonBackupRun):
    from django.contrib.auth import get_user_model
    from django.urls import reverse

    from apps.accounts import roles
    from apps.notifications.services import notify

    for u in get_user_model().objects.filter(is_active=True, groups__name=roles.ICT_OFFICER).distinct():
        notify(u, "Backup ready to download", f"{run.period_label} · {run.size / 1_048_576:.1f} MB. Any one of you can take it.", url=reverse("ict:backups"))
