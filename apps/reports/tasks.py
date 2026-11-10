from datetime import timedelta

from django.core.mail import EmailMessage
from django.conf import settings
from django.utils import timezone

from apps.core.jobs import daily, task


def _due(s, today):
    return s.frequency == "daily" or (s.frequency == "weekly" and today.weekday() == 0) or (s.frequency == "monthly" and today.day == 1)


@daily(hour=6, minute=30)
@task("reports.send_scheduled")
def send_scheduled():
    """E-mail each active schedule as an Excel file. Access is re-checked every time, so a removed role stops the delivery."""
    from . import export, registry
    from .models import ScheduledReport

    today, sent = timezone.localdate(), 0
    for s in ScheduledReport.objects.filter(active=True).select_related("user"):
        if not _due(s, today) or (s.last_run_at and timezone.localtime(s.last_run_at).date() == today):
            continue
        try:
            rep, cols, rows = registry.run(s.report_key, s.user, today - timedelta(days=s.range_days), today)
            msg = EmailMessage(f"{rep.title} · {today:%d %b %Y}", f"Your scheduled report '{rep.title}' ({len(rows)} rows) is attached.", settings.DEFAULT_FROM_EMAIL, [s.user.email])
            msg.attach(f"{rep.key}-{today:%Y%m%d}.xlsx", export.xlsx_bytes(rep.key, cols, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            msg.send(fail_silently=False)
            s.last_status = f"Sent {len(rows)} rows"
            sent += 1
        except Exception as e:  # keep going: one broken schedule must not stop the rest
            s.last_status = f"Failed: {str(e)[:120]}"
        s.last_run_at = timezone.now()
        s.save(update_fields=["last_run_at", "last_status"])
    return f"{sent} sent"
