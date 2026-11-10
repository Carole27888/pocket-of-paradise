"""One place that lists every report. Each report declares the module it belongs to, so access follows the same rules as the screens."""
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core.modules import user_can_access_module


@dataclass
class Report:
    key: str
    title: str
    module: str
    description: str
    run: Callable          # (user, start_date, end_date) -> (columns, rows)
    ranged: bool = True


REPORTS: dict[str, Report] = {}


def report(key, title, module, description, ranged=True):
    def deco(fn):
        REPORTS[key] = Report(key, title, module, description, fn, ranged)
        return fn
    return deco


def available(user):
    return [r for r in REPORTS.values() if user_can_access_module(user, r.module)]


def run(key, user, start=None, end=None):
    rep = REPORTS[key]
    if not user_can_access_module(user, rep.module):
        raise PermissionError("You don't have access to this report.")
    end = end or timezone.localdate()
    start = start or end - timedelta(days=30)
    cols, rows = rep.run(user, start, end)
    return rep, cols, rows


def _agent_scope(user):
    from apps.leads.views import visible_enquiries

    return visible_enquiries(user)


@report("leads_by_source", "Leads by source", "sales", "Enquiries created in the period, by channel, with the value in play.")
def leads_by_source(user, start, end):
    qs = _agent_scope(user).filter(created_at__date__range=(start, end)).values("source").annotate(n=Count("id"), value=Sum("deal_value")).order_by("-n")
    from apps.leads.models import Source

    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    return ["Source", "Enquiries", "Deal value (KES)"], [[labels.get(r["source"], r["source"]), r["n"], r["value"] or 0] for r in qs]


@report("pipeline_by_stage", "Pipeline by stage", "sales", "Where every open and closed enquiry sits right now.", ranged=False)
def pipeline_by_stage(user, start, end):
    from apps.leads.models import Stage

    labels = dict(Stage.choices)
    qs = _agent_scope(user).values("stage").annotate(n=Count("id"), value=Sum("deal_value"))
    order = {s: i for i, s in enumerate(Stage.values)}
    return ["Stage", "Enquiries", "Deal value (KES)"], [[labels[r["stage"]], r["n"], r["value"] or 0] for r in sorted(qs, key=lambda r: order.get(r["stage"], 99))]


@report("agent_performance", "Agent performance", "sales", "Enquiries handled, bookings won and value per sales agent.")
def agent_performance(user, start, end):
    if not user.can_manage_sales and not user.is_superuser:
        raise PermissionError("Managers only.")
    from apps.leads.models import Stage

    qs = _agent_scope(user).filter(created_at__date__range=(start, end)).exclude(assigned_agent=None).values("assigned_agent__full_name")
    rows = []
    for r in qs.annotate(n=Count("id"), booked=Count("id", filter=Q(stage__in=[Stage.DEPOSIT, Stage.CONFIRMED, Stage.COMPLETED])), value=Sum("deal_value")).order_by("-booked"):
        rows.append([r["assigned_agent__full_name"], r["n"], r["booked"], f"{round(100 * r['booked'] / r['n'])}%" if r["n"] else "0%", r["value"] or 0])
    return ["Agent", "Enquiries", "Booked", "Win rate", "Deal value (KES)"], rows


@report("bookings", "Bookings and balances", "sales", "Bookings created in the period with paid and outstanding amounts.")
def bookings(user, start, end):
    from apps.sales import services

    rows = [[b.reference, b.client.full_name, b.title, b.get_status_display(), b.currency, b.total_amount, b.amount_paid, b.balance]
            for b in services.bookings_for(user).filter(created_at__date__range=(start, end)).select_related("client").order_by("-created_at")]
    return ["Booking", "Client", "Trip", "Status", "Cur.", "Total", "Paid", "Balance"], rows


@report("outstanding_invoices", "Outstanding invoices", "finance", "Unpaid invoices, oldest first.", ranged=False)
def outstanding_invoices(user, start, end):
    from apps.finance.models import Invoice

    today = timezone.localdate()
    rows = [[i.number, i.client.full_name, i.due_date, (today - i.due_date).days if i.due_date < today else 0, i.currency, i.balance]
            for i in Invoice.objects.filter(status__in=["sent", "part_paid"]).select_related("client").order_by("due_date") if i.balance > 0]
    return ["Invoice", "Client", "Due", "Days overdue", "Cur.", "Balance"], rows


@report("fixed_assets", "Fixed asset register", "finance", "Cost, accumulated depreciation and net book value.", ranged=False)
def fixed_assets(user, start, end):
    from apps.assets.models import FixedAsset

    return (["Tag", "Asset", "Category", "Bought", "Cost", "Accumulated", "Net book value", "Status"],
            [[a.tag, a.name, a.get_category_display(), a.purchase_date, a.cost, a.accumulated(), a.book_value(), a.get_status_display()] for a in FixedAsset.objects.all()])


@report("leave_summary", "Leave taken", "hr", "Approved leave days per employee in the period.")
def leave_summary(user, start, end):
    from apps.hr.models import LeaveRequest

    agg = {}
    for r in LeaveRequest.objects.filter(status__in=LeaveRequest.COUNTED, start_date__range=(start, end)).select_related("employee__department", "leave_type"):
        row = agg.setdefault(r.employee_id, [r.employee.full_name, r.employee.department.name if r.employee.department else "", Decimal("0")])
        row[2] += r.days
    return ["Employee", "Department", "Days taken"], sorted(agg.values(), key=lambda x: -x[2])


@report("kyc_status", "Client KYC status", "deals", "Who has been verified, who is waiting and who has not started.", ranged=False)
def kyc_status(user, start, end):
    from apps.dealdesk.models import ClientKYC

    return ["Client", "ID type", "Status", "Verified by", "Verified on"], [[k.client.full_name, k.get_id_type_display(), k.get_status_display(), k.verified_by.full_name if k.verified_by else "", k.verified_at.date() if k.verified_at else ""]
                                                                     for k in ClientKYC.objects.select_related("client", "verified_by")]


@report("whatsapp_bot", "WhatsApp bot activity", "ict", "Chats, leads captured and hand-overs in the period.")
def whatsapp_bot(user, start, end):
    from apps.ict.models import BotConversation

    qs = BotConversation.objects.filter(started_at__date__range=(start, end))
    return ["Chats", "Leads captured", "Handed to a person"], [[qs.count(), qs.filter(lead_captured=True).count(), qs.exclude(handoff_reason="").count()]]


@report("data_requests", "Data-subject requests", "compliance", "Privacy requests received and their status.")
def data_requests(user, start, end):
    from apps.compliance.models import DataRequest

    return ["Reference", "Type", "Requester", "Received", "Due", "Status"], [[d.reference, d.get_kind_display(), d.requester_name, d.received_on, d.due_on, d.get_status_display()]
                                                                           for d in DataRequest.objects.filter(received_on__range=(start, end))]


@report("attendance", "Attendance", "hr", "Clock-ins, hours worked and where people clocked in from.")
def attendance_report(user, start, end):
    from apps.hr.models import AttendanceRecord

    rows = [[r.date, r.user.full_name, timezone.localtime(r.clock_in_at).strftime("%H:%M"), timezone.localtime(r.clock_out_at).strftime("%H:%M") if r.clock_out_at else "", r.hours,
             "Late" if r.late else "", "At office" if r.in_at_office else (f"{r.in_distance_m} m away" if r.in_distance_m is not None else "Location not shared")]
            for r in AttendanceRecord.objects.filter(date__range=(start, end)).select_related("user").order_by("-date", "user__full_name")]
    return ["Date", "Person", "In", "Out", "Hours", "Late", "Location"], rows
