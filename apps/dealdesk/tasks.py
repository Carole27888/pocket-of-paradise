from datetime import timedelta

from django.utils import timezone

from apps.core.jobs import daily, every, task


@daily(hour=8, minute=20)
@task("dealdesk.instalment_reminders")
def instalment_reminders():
    """Message clients about instalments due in 3 days or overdue (once a day at most)."""
    from apps.integrations.messaging import send_message

    from .models import Instalment

    today, n = timezone.localdate(), 0
    qs = Instalment.objects.filter(due_date__lte=today + timedelta(days=3)).exclude(reminded_on=today).select_related("plan__booking__enquiry__client")
    for ins in qs:
        if ins.outstanding <= 0:
            continue
        b = ins.plan.booking
        c = b.enquiry.client
        send_message("sms", c.phone, f"Hello {c.full_name.split()[0]}, instalment of {b.currency} {ins.outstanding:,.0f} for {b.reference} is due {ins.due_date:%d %b}. Thank you, Pocket of Paradise.",
                     purpose="instalment_reminder", related=f"booking:{b.pk}")
        ins.reminded_on = today
        ins.save(update_fields=["reminded_on"])
        n += 1
    return f"{n} reminders"


@every(30)
@task("dealdesk.missed_callbacks")
def missed_callbacks():
    """A 'busy / call back later' or unanswered call with no later call or open follow-up gets a callback follow-up after 2 hours."""
    from apps.sales.models import CallLog, FollowUp

    n = 0
    cutoff = timezone.now() - timedelta(hours=2)
    for c in CallLog.objects.filter(outcome__in=["busy", "no_answer"], called_at__lt=cutoff, called_at__gt=timezone.now() - timedelta(days=2)).select_related("enquiry"):
        e = c.enquiry
        if CallLog.objects.filter(enquiry=e, called_at__gt=c.called_at).exists() or FollowUp.objects.filter(enquiry=e, done_at__isnull=True).exists():
            continue
        FollowUp.objects.create(enquiry=e, agent=c.agent or e.assigned_agent, kind="call", title="Call back — missed earlier", due_at=timezone.now())
        n += 1
    return f"{n} callbacks created"
