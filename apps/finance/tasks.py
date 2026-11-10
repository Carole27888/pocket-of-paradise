"""Scheduled finance jobs: reminders, recurring invoices, KRA refresh, deadline alerts."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts import roles
from apps.core.jobs import daily, monthly, task
from apps.core.money import format_money
from apps.integrations.messaging import send_message
from apps.notifications.services import notify_roles

from .models import Invoice, KraObligation

BALANCE_REMINDER_DAYS = (30, 14, 7, 2)   # days before travel


def send_invoice_reminder(inv: Invoice, via: str = "sms", purpose: str = "invoice_reminder"):
    c = inv.client
    body = (f"Hello {c.full_name.split()[0]}, a friendly reminder: invoice {inv.number} balance "
            f"{format_money(inv.balance, inv.currency)} is due {inv.due_date:%d %b %Y}. Pay via M-PESA Paybill "
            f"with account {inv.number}. Thank you, Pocket of Paradise Travel Company.")
    target = c.email if via == "email" else c.phone
    msg = send_message(via, target, body, subject=f"Reminder: invoice {inv.number}", purpose=purpose,
                       related=f"invoice:{inv.pk}")
    inv.last_reminder_at, inv.reminders_sent = timezone.now(), inv.reminders_sent + 1
    inv.save(update_fields=["last_reminder_at", "reminders_sent"])
    return msg


@daily(hour=8, minute=0)
@task("finance.invoice_reminders")
def invoice_reminders():
    """Balance-due reminders 30/14/7/2 days before travel; escalations at 7 and 30 days overdue."""
    today = timezone.localdate()
    sent = 0
    for inv in Invoice.objects.filter(status__in=["sent", "part_paid"]).select_related("client", "booking__agent__user"):
        travel = inv.booking.travel_start if inv.booking_id else None
        days_to_travel = (travel - today).days if travel else None
        due_reminder = days_to_travel in BALANCE_REMINDER_DAYS
        overdue = (today - inv.due_date).days
        if inv.last_reminder_at and timezone.localtime(inv.last_reminder_at).date() == today:
            continue
        if due_reminder or overdue in (1, 7, 14, 30):
            send_invoice_reminder(inv, "sms")
            sent += 1
        if overdue == 7 and inv.booking_id and inv.booking.agent_id and inv.booking.agent.user_id:
            from apps.notifications.services import notify

            notify(inv.booking.agent.user, f"Invoice {inv.number} is 7 days overdue",
                   f"{inv.client.full_name} · {format_money(inv.balance, inv.currency)}", inv.booking.get_absolute_url(),
                   "warning", dedupe_key=f"overdue7:{inv.pk}")
        if overdue == 30:
            notify_roles([roles.FINANCE, roles.ADMIN], f"Demand letter due: {inv.number}",
                         f"{inv.client.full_name} is 30 days overdue.", inv.get_absolute_url(), "danger",
                         dedupe_key=f"demand:{inv.pk}")
    return f"{sent} reminders"


@daily(hour=6, minute=30)
@task("finance.recurring_invoices")
def recurring_invoices():
    from . import services

    today = timezone.localdate()
    made = 0
    for inv in Invoice.objects.filter(recur_every_months__gt=0, next_run_date__lte=today).exclude(status="void"):
        new = services.create_invoice(
            inv.client, lines=[{"description": l.description, "quantity": l.quantity, "unit_price": l.unit_price,
                                "vat_applicable": l.vat_applicable} for l in inv.lines.all()],
            currency=inv.currency, vat_rate=inv.vat_rate, prices_include_vat=inv.prices_include_vat,
            issue_date=today, due_date=today + (inv.due_date - inv.issue_date), notes=inv.notes)
        services.issue_invoice(new)
        inv.next_run_date = inv.next_run_date + timedelta(days=30 * inv.recur_every_months)
        inv.save(update_fields=["next_run_date"])
        made += 1
    return f"{made} recurring invoices"


@monthly(day=1, hour=5)
@task("finance.refresh_kra")
def refresh_kra():
    from . import services

    last = (timezone.localdate().replace(day=1) - timedelta(days=1))
    services.refresh_obligations(last)
    services.refresh_obligations(timezone.localdate())
    return "ok"


@daily(hour=7, minute=30)
@task("finance.deadline_alerts")
def deadline_alerts():
    """KRA obligations due in 3 days, bills due soon, over-budget lines."""
    from apps.suppliers.models import Bill

    today = timezone.localdate()
    n = 0
    for o in KraObligation.objects.exclude(status="filed").filter(due_date=today + timedelta(days=3)):
        n += notify_roles([roles.FINANCE, roles.ADMIN], f"{o.get_tax_display()} due in 3 days",
                          f"{format_money(o.amount)} due {o.due_date:%d %b}", "/finance/kra/", "warning",
                          dedupe_key=f"kra3:{o.pk}")
    for b in Bill.objects.filter(status__in=["approved", "scheduled"], due_date__lte=today + timedelta(days=2)):
        n += notify_roles([roles.FINANCE], f"Bill {b.number} due {b.due_date:%d %b}",
                          f"{b.supplier.name} · {format_money(b.balance, b.currency)}", b.get_absolute_url(),
                          "warning", dedupe_key=f"bill:{b.pk}:{b.due_date}")
    from . import services

    for r in services.budget_vs_actual(today.year, "month", today.month):
        if r["status"] == "Over budget":
            n += notify_roles([roles.FINANCE, roles.ADMIN], "Over budget: " + r["budget"].category.replace("_", " "),
                              f"{r['used']}% of the monthly budget used", "/finance/budgets/", "danger",
                              dedupe_key=f"budget:{r['budget'].pk}:{today:%Y-%m}")
    return f"{n} alerts"


@monthly(day=1, hour=7, minute=0)
@task("finance.management_pack")
def management_pack():
    """E-mail the Directors last month's headline numbers with the income statement attached (CSV)."""
    import csv
    import io

    from django.core.mail import EmailMessage

    from apps.accounts.models import User

    from . import reports, services

    today = timezone.localdate()
    end = today.replace(day=1) - timedelta(days=1)
    start = end.replace(day=1)
    pnl = services.income_statement(start, end)
    title, headers, rows = reports.income_statement(start, end)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    body = (f"Management pack · {start:%B %Y}\n\nRevenue: {format_money(pnl['revenue'])}\nExpenses: {format_money(pnl['expenses'])}\n"
            f"Net profit: {format_money(pnl['net_profit'])}\nCash & bank: {format_money(services.cash_position(end))}\n"
            f"Owed to us: {format_money(services.receivables_aging()['total'])}\nOwed to suppliers: {format_money(services.payables_aging()['total'])}\n\n"
            "Full statements: /finance/statements/")
    sent = 0
    for u in User.objects.filter(is_active=True, groups__name=roles.DIRECTOR).distinct():
        msg = EmailMessage(f"Pocket of Paradise management pack · {start:%B %Y}", body, to=[u.email])
        msg.attach(f"income-statement-{start:%Y-%m}.csv", buf.getvalue(), "text/csv")
        msg.send(fail_silently=True)
        sent += 1
    return f"sent to {sent} director(s)"
