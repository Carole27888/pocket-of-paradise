"""Report library: each report returns (title, headers, rows[, note]) for HTML, CSV and Excel."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from apps.core.money import format_money

from . import services
from .models import Invoice, KraObligation, PayrollRun

D = Decimal


def _m(v):
    return format_money(v, "KES")


def income_statement(start, end):
    s = services.income_statement(start, end)
    rows = []
    for group in ("Revenue", "Cost of sales", "Operating expenses", "Tax"):
        for r in s["sections"].get(group, []):
            rows.append([group, r["account"].name, _m(r["amount"])])
        if group == "Cost of sales":
            rows.append(["", "Gross profit", _m(s["gross_profit"])])
    rows.append(["", "Net profit", _m(s["net_profit"])])
    return "Income statement", ["Section", "Account", "Amount"], rows


def balance_sheet(start, end):
    b = services.balance_sheet(end)
    rows = []
    for t, label in (("asset", "Assets"), ("liability", "Liabilities"), ("equity", "Equity")):
        for group, items in b["groups"][t].items():
            for r in items:
                rows.append([label, r["account"].name, _m(r["amount"])])
    rows += [["Equity", "Current-year earnings", _m(b["current_earnings"])], ["", "Total assets", _m(b["assets"])],
             ["", "Liabilities + equity", _m(b["liabilities"] + b["equity"])], ["", "Check (should be 0)", _m(b["check"])]]
    return f"Balance sheet as at {end:%d %b %Y}", ["Section", "Account", "Amount"], rows


def cash_flow(start, end):
    c = services.cash_flow(start, end)
    rows = [["Operating", "Net profit", _m(c["net_profit"])], ["Operating", "Depreciation", _m(c["depreciation"])]]
    rows += [["Operating", k, _m(v)] for k, v in c["working_capital"]]
    rows += [["", "Net cash from operations", _m(c["operating"])], ["", "Investing", _m(c["investing"])],
             ["", "Financing", _m(c["financing"])], ["", "Net change in cash", _m(c["net_change"])],
             ["", "Opening cash", _m(c["opening_cash"])], ["", "Closing cash", _m(c["closing_cash"])],
             ["", "Check (should be 0)", _m(c["check"])]]
    return "Cash flow statement (indirect)", ["Section", "Item", "Amount"], rows


def aged_receivables(start, end):
    ag = services.receivables_aging()
    rows = [[i.number, i.client.full_name, i.due_date.strftime("%d %b %Y"), i.days_overdue, _m(i.balance_kes)]
            for i in sorted(ag["invoices"], key=lambda x: -x.days_overdue)]
    rows.append(["", "Total", "", "", _m(ag["total"])])
    return "Aged receivables", ["Invoice", "Client", "Due", "Days overdue", "Balance (KES)"], rows


def aged_payables(start, end):
    from apps.suppliers.models import Bill

    today = timezone.localdate()
    rows = [[b.number, b.supplier.name, b.due_date.strftime("%d %b %Y"), max((today - b.due_date).days, 0),
             _m(b.balance * b.fx_rate)] for b in Bill.objects.exclude(status__in=["paid", "void"]).select_related("supplier")]
    return "Aged payables", ["Bill", "Supplier", "Due", "Days overdue", "Balance (KES)"], rows


def kra_summary(start, end):
    rows = [[o.get_tax_display(), o.period.strftime("%b %Y"), _m(o.amount), o.due_date.strftime("%d %b %Y"),
             o.get_status_display()] for o in KraObligation.objects.filter(period__range=(start.replace(day=1), end))]
    return "KRA tax summary", ["Tax", "Period", "Amount", "Due", "Status"], rows


def payroll_summary(start, end):
    rows = []
    for run in PayrollRun.objects.filter(period__range=(start.replace(day=1), end)):
        t = run.totals()
        rows.append([run.period.strftime("%B %Y"), t["employees"], _m(t["gross"]), _m(t["paye"]), _m(t["nssf"] + t["nssf_employer"]),
                     _m(t["shif"]), _m(t["ahl"] + t["ahl_employer"]), _m(t["net"])])
    return "Payroll summary", ["Month", "Staff", "Gross", "PAYE", "NSSF", "SHIF", "Housing levy", "Net pay"], rows


def budget_vs_actual(start, end):
    rows = []
    for r in services.budget_vs_actual(end.year, "month", end.month):
        rows.append([r["budget"].category_label, _m(r["budget"].amount), _m(r["actual"]),
                     f"{r['used']}%", r["status"]])
    return f"Budget vs actual · {end:%B %Y}", ["Category", "Budget", "Actual", "Used", "Status"], rows


def sales_by_tour(start, end):
    from apps.sales.models import Booking, BookingStatus

    agg = defaultdict(lambda: [0, D("0")])
    for b in Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(
            booked_at__date__range=(start, end)).select_related("package"):
        k = b.package.title if b.package else (b.title or "Custom")
        agg[k][0] += 1
        agg[k][1] += b.total_kes
    rows = [[k, v[0], _m(v[1])] for k, v in sorted(agg.items(), key=lambda kv: -kv[1][1])]
    return "Sales by tour", ["Tour", "Bookings", "Value (KES)"], rows


def sales_by_channel(start, end):
    from apps.leads.models import Source
    from apps.sales.models import Booking, BookingStatus

    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    agg = defaultdict(lambda: [0, D("0")])
    for b in Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(
            booked_at__date__range=(start, end)).select_related("enquiry"):
        k = labels.get(b.enquiry.source, b.enquiry.source)
        agg[k][0] += 1
        agg[k][1] += b.total_kes
    return "Sales by channel", ["Channel", "Bookings", "Value (KES)"], [
        [k, v[0], _m(v[1])] for k, v in sorted(agg.items(), key=lambda kv: -kv[1][1])]


def profitability_by_departure(start, end):
    from apps.trips.models import Departure

    rows = []
    for d in Departure.objects.filter(start_date__range=(start, end)).exclude(stage="cancelled").select_related("package"):
        revenue = sum((b.total_kes for b in d.live_bookings), D("0"))
        costs = sum((x.amount * x.fx_rate for x in d.bills.exclude(status="void")), D("0")) + sum(
            (e.amount for e in d.expenses.exclude(status="rejected")), D("0"))
        rows.append([d.reference, d.package.title, d.start_date.strftime("%d %b %Y"), _m(revenue), _m(costs), _m(revenue - costs)])
    return "Profitability by departure", ["Departure", "Tour", "Date", "Revenue", "Costs", "Margin"], rows


def supplier_spend(start, end):
    from apps.suppliers.models import Bill

    agg = defaultdict(D)
    for b in Bill.objects.exclude(status="void").filter(bill_date__range=(start, end)).select_related("supplier"):
        agg[b.supplier.name] += b.amount_kes
    return "Supplier spend", ["Supplier", "Billed (KES)"], [[k, _m(v)] for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]


REPORTS = {
    "income-statement": ("Income statement", income_statement, "Revenue less costs for the period"),
    "balance-sheet": ("Balance sheet", balance_sheet, "Assets, liabilities and equity at period end"),
    "cash-flow": ("Cash flow statement", cash_flow, "Indirect method"),
    "aged-receivables": ("Aged receivables", aged_receivables, "What clients owe, by age"),
    "aged-payables": ("Aged payables", aged_payables, "What we owe suppliers, by age"),
    "kra-summary": ("KRA tax summary", kra_summary, "PAYE, VAT, levies and instalments"),
    "payroll-summary": ("Payroll summary", payroll_summary, "Monthly payroll totals"),
    "budget-vs-actual": ("Budget vs actual", budget_vs_actual, "Month of the end date"),
    "sales-by-tour": ("Sales by tour", sales_by_tour, "Bookings and value per tour"),
    "sales-by-channel": ("Sales by channel", sales_by_channel, "Which channels bring revenue"),
    "profitability-by-departure": ("Profitability by departure", profitability_by_departure, "Revenue vs trip costs"),
    "supplier-spend": ("Supplier spend", supplier_spend, "Billed amount per supplier"),
}
