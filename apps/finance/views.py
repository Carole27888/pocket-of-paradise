import calendar
import csv
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.models import CompanySetting
from apps.core.money import amount_in_words, format_money
from apps.core.pdf import render_pdf
from apps.core.tables import Col, TableScreen
from apps.sales import analytics as sales_analytics
from apps.suppliers.models import Bill

from . import reports as report_lib
from . import services
from .forms import (BillPayForm, BudgetForm, EventForm, ExpenseForm, InvoiceForm, InvoiceLineFormSet, MatchForm,
                    MpesaImportForm, PayrollLineForm, PayrollRunForm, ReceiptPaymentForm, TaxSettingForm)
from .models import (Budget, Expense, FinanceEvent, Invoice, KraObligation, PayrollLine, PayrollRun, Receipt,
                     TaxSetting, q2)

FIN = "finance"
INV_TONE = {"draft": "neutral", "sent": "info", "part_paid": "warning", "paid": "success", "void": "danger"}
EXP_TONE = {"pending": "warning", "approved": "info", "paid": "success", "rejected": "danger"}
BILL_TONE = {"awaiting": "warning", "approved": "info", "scheduled": "info", "paid": "success", "void": "danger"}
KRA_TONE = {"draft": "neutral", "ready": "warning", "filed": "success", "scheduled": "info"}


def _need_manage(user):
    if not user.can_manage_finance:
        raise PermissionDenied


def _range(request):
    return sales_analytics.period(request.GET.get("range", "month"))


def _ctx(request, **kw):
    start, end, key = _range(request)
    return {"ranges": sales_analytics.RANGES, "range_key": key, "start": start, "end": end, **kw}


def _errors(form):
    return "; ".join(e for errs in form.errors.values() for e in errs)


def _try(request, fn, *a, ok="Done.", **kw):
    try:
        res = fn(*a, **kw)
        messages.success(request, ok)
        return res
    except ValueError as exc:
        messages.error(request, str(exc))


# ================================================================================================ dashboard
@module_required(FIN)
def dashboard(request):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    today = timezone.localdate()
    pnl = services.income_statement(start, end)
    prev_len = (end - start).days + 1
    prev = services.income_statement(start - timedelta(days=prev_len), start - timedelta(days=1))
    bs = services.balance_sheet(end)
    ar, ap = services.receivables_aging(), services.payables_aging()
    series = services.month_series(6)
    exp_rows = [(r["account"].name, float(r["amount"])) for g in ("Cost of sales", "Operating expenses")
                for r in pnl["sections"].get(g, [])]
    exp_rows.sort(key=lambda x: -x[1])
    upcoming = []
    for o in KraObligation.objects.exclude(status="filed").filter(due_date__lte=today + timedelta(days=21)):
        upcoming.append((o.due_date, f"{o.get_tax_display()} {o.period:%b}", o.amount, "kra", o.is_overdue))
    for b in Bill.objects.filter(status__in=["approved", "scheduled"], due_date__lte=today + timedelta(days=14)):
        upcoming.append((b.due_date, f"Bill {b.number} · {b.supplier}", b.balance * b.fx_rate, "bill", b.is_overdue))
    upcoming.sort(key=lambda x: x[0])
    from apps.trips.models import Departure

    soon = Departure.objects.filter(start_date__range=(today, today + timedelta(days=21)), stage__in=["planned", "ready"])
    need_prepay = [d for d in soon.select_related("package") if not d.bills.filter(prepayment=True).exclude(status="void").exists()]
    budget_rows = services.budget_vs_actual(today.year, "month", today.month)[:5]
    ctx.update(
        pnl=pnl, prev=prev, bs=bs, ar=ar, ap=ap, cash=services.cash_position(end),
        profit_delta=pnl["net_profit"] - prev["net_profit"],
        charts={"cash": {"labels": [s[0] for s in series], "income": [float(s[1]) for s in series],
                         "expenses": [float(s[2]) for s in series], "values": [float(s[1]) for s in series]},
                "expense": {"labels": [e[0] for e in exp_rows[:7]], "values": [e[1] for e in exp_rows[:7]]},
                "aging": {"labels": list(ar["buckets"]), "values": [float(v) for v in ar["buckets"].values()]}},
        recent=services.JournalEntry.objects.prefetch_related("lines")[:8], upcoming=upcoming[:8],
        need_prepay=need_prepay[:5], budget_rows=budget_rows,
        kra=KraObligation.objects.exclude(status="filed").order_by("due_date")[:5],
        kra_total=sum((o.amount for o in KraObligation.objects.exclude(status="filed")), Decimal("0")),
        can_manage=request.user.can_manage_finance)
    return render(request, "finance/dashboard.html", ctx)


# ================================================================================================ revenue
@module_required(FIN)
def revenue(request):
    from apps.sales.models import Booking, BookingStatus

    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    pnl = services.income_statement(start, end)
    streams = [(r["account"].name, r["amount"]) for r in pnl["sections"].get("Revenue", [])]
    deposits = services._bal(services.account("deposits_held"), services._balances(None, end))
    series = services.month_series(6)
    bookings = (Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(
        booked_at__date__range=(start, end)).select_related("client", "agent", "package").prefetch_related("payments")
        .order_by("-booked_at")[:50])
    invoiced = sum((i.total_kes for i in Invoice.objects.filter(issue_date__range=(start, end)).exclude(status="void")), Decimal("0"))
    ctx.update(pnl=pnl, streams=streams, deposits=deposits, invoiced=invoiced, bookings=bookings,
               charts={"trend": {"labels": [s[0] for s in series], "values": [float(s[1]) for s in series]},
                       "streams": {"labels": [s[0] for s in streams], "values": [float(s[1]) for s in streams]}})
    return render(request, "finance/revenue.html", ctx)


# ================================================================================================ receivables
@module_required(FIN)
def receivables(request):
    ar = services.receivables_aging()
    today = timezone.localdate()
    month_start = today.replace(day=1)
    collected = Receipt.objects.filter(received_at__date__gte=month_start).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    overdue30 = sum((i.balance_kes for i in ar["invoices"] if i.days_overdue > 30), Decimal("0"))
    paid = Invoice.objects.filter(status="paid")
    avg_days = None
    if paid.exists():
        spans = []
        for inv in paid.prefetch_related("receipts")[:200]:
            last = max((r.received_at for r in inv.receipts.all()), default=None)
            if last:
                spans.append((timezone.localtime(last).date() - inv.issue_date).days)
        avg_days = round(sum(spans) / len(spans)) if spans else None
    debtors = sorted(ar["invoices"], key=lambda i: -i.days_overdue)
    return render(request, "finance/receivables.html", {
        "ar": ar, "collected": collected, "overdue30": overdue30, "avg_days": avg_days, "debtors": debtors[:40],
        "charts": {"aging": {"labels": list(ar["buckets"]), "values": [float(v) for v in ar["buckets"].values()]}},
        "can_manage": request.user.can_manage_finance})


@require_POST
@module_required(FIN)
def remind(request, pk):
    _need_manage(request.user)
    inv = get_object_or_404(Invoice, pk=pk)
    from .tasks import send_invoice_reminder

    send_invoice_reminder(inv, via=request.POST.get("via", "sms"))
    messages.success(request, f"Reminder sent for {inv.number}.")
    return redirect(request.POST.get("next") or "finance:receivables")


# ================================================================================================ invoices
class InvoiceList(TableScreen):
    module_key = FIN
    model = Invoice
    tabs_field = "status"
    tabs_choices = Invoice.STATUSES
    search_fields = ["number", "client__full_name", "client__phone", "notes"]
    sort_fields = ["issue_date", "due_date", "total", "number"]
    default_sort = "-issue_date"
    title = "Invoicing"
    subtitle = "Invoices raised from bookings or by hand. Paybill account number = the invoice number."
    placeholder = "Search invoice, client, phone…"
    export_name = "invoices"
    columns = [
        Col("Invoice", "number", sort="number", link=True, sub="client"),
        Col("Issued", "issue_date", sort="issue_date", kind="date"),
        Col("Due", "due_date", sort="due_date", kind="date"),
        Col("Total", "total", sort="total", kind="money", currency="currency"),
        Col("Balance", "balance", kind="money", currency="currency"),
        Col("Status", "display_status", kind="pill",
            tone=lambda o: "danger" if o.is_overdue else INV_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Invoice.objects.select_related("client")
        self._unfiltered = qs
        val = self.request.GET.get("status", "")
        if val == "overdue":
            return qs.filter(status__in=["sent", "part_paid"], due_date__lt=timezone.localdate())
        return qs.filter(status=val) if val else qs

    def actions(self):
        return [{"label": "New invoice", "url": reverse("finance:invoice_new"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_finance else []

    def kpis(self, qs):
        open_ = qs.filter(status__in=["sent", "part_paid"])
        today = timezone.localdate()
        return [{"label": "Invoiced (all time)", "value": f"{qs.exclude(status='void').count()}", "accent": True},
                {"label": "Outstanding", "value": format_money(sum((i.balance_kes for i in open_), Decimal("0")))},
                {"label": "Overdue", "value": open_.filter(due_date__lt=today).count(), "danger": True},
                {"label": "Draft", "value": qs.filter(status="draft").count()}]


def _invoice_formset(request, inv, data=None):
    return InvoiceLineFormSet(data, instance=inv)


@module_required(FIN)
def invoice_edit(request, pk=None):
    _need_manage(request.user)
    inv = get_object_or_404(Invoice, pk=pk) if pk else None
    if inv and inv.status != Invoice.DRAFT:
        messages.warning(request, "Only draft invoices can be edited.")
        return redirect(inv)
    form = InvoiceForm(request.POST or None, instance=inv, initial={"due_date": timezone.localdate() + timedelta(days=14)})
    formset = InvoiceLineFormSet(request.POST or None, instance=inv)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        inv = form.save(commit=False)
        from apps.sales.services import fx_rate

        inv.fx_rate = fx_rate(inv.currency)
        inv.created_by = inv.created_by or request.user
        if inv.recur_every_months and not inv.next_run_date:
            inv.next_run_date = inv.issue_date + timedelta(days=30 * inv.recur_every_months)
        inv.save()
        formset.instance = inv
        formset.save()
        inv.recalculate()
        messages.success(request, f"{inv.number} saved · {inv.currency} {inv.total:,.2f}")
        return redirect(inv)
    return render(request, "finance/invoice_form.html", {"form": form, "formset": formset, "inv": inv})


@module_required(FIN)
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("client", "booking"), pk=pk)
    return render(request, "finance/invoice_detail.html", {
        "inv": inv, "lines": inv.lines.all(), "receipts": inv.receipts.all(), "can_manage": request.user.can_manage_finance,
        "pay_form": ReceiptPaymentForm(initial={"amount": inv.balance}), "company": CompanySetting.load()})


@require_POST
@module_required(FIN)
def invoice_action(request, pk):
    _need_manage(request.user)
    inv = get_object_or_404(Invoice, pk=pk)
    action = request.POST.get("action")
    if action == "issue":
        _try(request, services.issue_invoice, inv, request.user, ok=f"{inv.number} issued.")
    elif action == "void":
        _try(request, services.void_invoice, inv, request.user, ok=f"{inv.number} voided.")
    elif action == "etims":
        inv.etims_cu_number = request.POST.get("cu", "").strip()[:40]
        inv.save(update_fields=["etims_cu_number"])
        messages.success(request, "eTIMS number saved.")
    return redirect(inv)


@require_POST
@module_required(FIN)
def invoice_payment(request, pk):
    _need_manage(request.user)
    inv = get_object_or_404(Invoice, pk=pk)
    form = ReceiptPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, _errors(form))
        return redirect(inv)
    d = form.cleaned_data
    if inv.booking_id:   # the booking's own payment path also records the receipt (single source of truth)
        from apps.sales.services import record_payment

        _try(request, record_payment, inv.booking, request.user, amount=d["amount"], method=d["method"] if d["method"] in ("mpesa", "bank", "card", "cash") else "bank",
             reference=d["reference"], received_at=d["received_at"], note=d["note"], ok="Payment recorded.")
    else:
        _try(request, services.record_receipt, inv, amount=d["amount"], method=d["method"], reference=d["reference"],
             received_at=d["received_at"], note=d["note"], user=request.user, ok="Payment recorded.")
    return redirect(inv)


def _doc_ctx(inv):
    return {"inv": inv, "lines": inv.lines.all(), "company": CompanySetting.load(),
            "words": amount_in_words(inv.total, inv.currency)}


@module_required(FIN)
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    return render(request, "finance/invoice_print.html", {**_doc_ctx(inv), "print": request.GET.get("print") == "1"})


@module_required(FIN)
def invoice_pdf(request, pk):
    inv = get_object_or_404(Invoice, pk=pk)
    return render_pdf(request, "finance/invoice_print.html", _doc_ctx(inv), f"{inv.number}.pdf",
                      f"{reverse('finance:invoice_print', args=[pk])}?print=1")


# ================================================================================================ receipts
class ReceiptList(TableScreen):
    module_key = FIN
    model = Receipt
    search_fields = ["number", "reference", "account_ref", "invoice__number", "client__full_name"]
    sort_fields = ["received_at", "amount", "number"]
    default_sort = "-received_at"
    title = "Receipts & Collections"
    subtitle = "Official receipts. Unmatched M-PESA payments wait here until matched to an invoice."
    placeholder = "Search receipt, code, invoice, client…"
    export_name = "receipts"
    columns = [
        Col("Receipt", "number", link=True, sub="client"),
        Col("Received", "received_at", sort="received_at", kind="datetime"),
        Col("Invoice", "invoice", sub="account_ref"),
        Col("Method", "get_method_display", sub="reference"),
        Col("Amount", "amount", sort="amount", kind="money", currency="currency"),
        Col("Matched", "match_label", kind="pill", tone=lambda o: "success" if o.is_matched else "warning"),
    ]

    def get_base_queryset(self):
        qs = Receipt.objects.select_related("invoice", "client")
        if self.request.GET.get("unmatched"):
            qs = qs.filter(invoice__isnull=True)
        return qs

    def actions(self):
        if not self.request.user.can_manage_finance:
            return []
        return [{"label": "Import M-PESA statement", "url": reverse("finance:mpesa_import"), "style": "accent", "icon": "download"}]

    def kpis(self, qs):
        month = qs.filter(received_at__date__gte=timezone.localdate().replace(day=1))
        return [{"label": "Collected this month", "value": f"KES {sum((r.amount_kes for r in month), Decimal('0')):,.0f}", "accent": True},
                {"label": "Receipts this month", "value": month.count()},
                {"label": "Unmatched", "value": qs.filter(invoice__isnull=True).count(), "danger": True},
                {"label": "M-PESA share", "value": f"{round(100 * month.filter(method='mpesa').count() / month.count()) if month.count() else 0}%"}]




@module_required(FIN)
def receipt_detail(request, pk):
    r = get_object_or_404(Receipt.objects.select_related("invoice", "client"), pk=pk)
    return render(request, "finance/receipt_detail.html", {
        "r": r, "can_manage": request.user.can_manage_finance, "match_form": MatchForm() if not r.is_matched else None,
        "words": amount_in_words(r.amount, r.currency)})


def _receipt_ctx(r):
    return {"r": r, "company": CompanySetting.load(), "words": amount_in_words(r.amount, r.currency)}


@module_required(FIN)
def receipt_print(request, pk):
    r = get_object_or_404(Receipt, pk=pk)
    return render(request, "finance/receipt_print.html", {**_receipt_ctx(r), "print": request.GET.get("print") == "1"})


@module_required(FIN)
def receipt_pdf(request, pk):
    r = get_object_or_404(Receipt, pk=pk)
    return render_pdf(request, "finance/receipt_print.html", _receipt_ctx(r), f"{r.number}.pdf",
                      f"{reverse('finance:receipt_print', args=[pk])}?print=1")


@require_POST
@module_required(FIN)
def receipt_match(request, pk):
    _need_manage(request.user)
    r = get_object_or_404(Receipt, pk=pk)
    form = MatchForm(request.POST)
    if form.is_valid():
        _try(request, services.match_receipt, r, form.cleaned_data["invoice"], request.user, ok="Matched to invoice.")
    else:
        messages.error(request, "Choose an open invoice.")
    return redirect(r)


@module_required(FIN)
def mpesa_import(request):
    _need_manage(request.user)
    form = MpesaImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        text = form.cleaned_data["file"].read().decode("utf-8-sig", errors="replace")
        result = services.import_mpesa_csv(text, request.user)
        messages.success(request, f"{result['created']} imported, {result['matched']} matched to invoices, "
                                  f"{result['duplicates']} duplicates, {result['invalid']} invalid.")
        return redirect("finance:receipts")
    return render(request, "components/form_page.html", {
        "form": form, "title": "Import M-PESA statement",
        "subtitle": "Upload a CSV with columns date (ISO), amount, reference, account. Payments whose account equals an invoice number are matched automatically.",
        "cancel_url": reverse("finance:receipts")})


# ================================================================================================ payables
class PayablesList(TableScreen):
    module_key = FIN
    model = Bill
    tabs_field = "status"
    tabs_choices = Bill.STATUSES
    search_fields = ["number", "supplier__name", "supplier_ref", "description", "departure__reference"]
    sort_fields = ["due_date", "amount", "number"]
    default_sort = "due_date"
    template_name = "finance/payables.html"
    title = "Supplier Payables"
    subtitle = "Bills from lodges, camps, transporters and park-fee agents: approve, schedule and pay."
    placeholder = "Search bill, supplier, departure…"
    export_name = "bills"
    columns = [
        Col("Bill", "number", link=True, sub="supplier"),
        Col("Departure", "departure", sub="get_category_display"),
        Col("Due", "due_date", sort="due_date", kind="date"),
        Col("Amount", "amount", sort="amount", kind="money", currency="currency"),
        Col("Balance", "balance", kind="money", currency="currency"),
        Col("Status", "due_label", kind="pill",
            tone=lambda o: "danger" if o.is_overdue else BILL_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Bill.objects.select_related("supplier", "departure").prefetch_related("payments")
        self._unfiltered = qs
        val = self.request.GET.get("status", "")
        return qs.filter(status=val) if val else qs

    def kpis(self, qs):
        open_ = [b for b in qs.exclude(status__in=["paid", "void"])]
        today = timezone.localdate()
        return [{"label": "Outstanding", "value": f"KES {sum((b.balance * b.fx_rate for b in open_), Decimal('0')):,.0f}", "accent": True},
                {"label": "Awaiting approval", "value": qs.filter(status="awaiting").count()},
                {"label": "Due this week", "value": sum(1 for b in open_ if today <= b.due_date <= today + timedelta(days=7))},
                {"label": "Overdue", "value": sum(1 for b in open_ if b.due_date < today), "danger": True}]

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["aging"] = services.payables_aging()
        return ctx


@require_POST
@module_required(FIN)
def bill_action(request, pk):
    _need_manage(request.user)
    bill = get_object_or_404(Bill, pk=pk)
    action = request.POST.get("action")
    if action == "approve":
        _try(request, services.approve_bill, bill, request.user, ok=f"{bill.number} approved.")
    elif action == "schedule":
        d = request.POST.get("date")
        try:
            bill.scheduled_for = date.fromisoformat(d)
            bill.status = Bill.SCHEDULED
            bill.save(update_fields=["scheduled_for", "status", "updated_at"])
            messages.success(request, f"Payment scheduled for {bill.scheduled_for:%d %b}.")
        except (TypeError, ValueError):
            messages.error(request, "Choose a date.")
    elif action == "pay":
        form = BillPayForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            _try(request, services.pay_bill, bill, request.user, amount=d["amount"], method=d["method"],
                 reference=d["reference"], ok="Payment recorded.")
        else:
            messages.error(request, _errors(form))
    elif action == "void" and bill.status == Bill.AWAITING:
        bill.status = Bill.VOID
        bill.save(update_fields=["status", "updated_at"])
        messages.success(request, "Bill voided.")
    return redirect(request.POST.get("next") or bill)


@require_POST
@module_required(FIN)
def payment_run(request):
    """Pay every approved/scheduled bill that is ticked (or due by the chosen date) in one go."""
    _need_manage(request.user)
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    bills = Bill.objects.filter(pk__in=ids, status__in=[Bill.APPROVED, Bill.SCHEDULED])
    n = 0
    for b in bills:
        try:
            services.pay_bill(b, request.user, method=request.POST.get("method", "bank"), reference="Payment run")
            n += 1
        except ValueError:
            pass
    messages.success(request, f"{n} bill(s) paid in the run.")
    return redirect("finance:payables")


# ================================================================================================ expenses & KRA
class ExpenseList(TableScreen):
    module_key = FIN
    model = Expense
    tabs_field = "status"
    tabs_choices = Expense.STATUSES
    search_fields = ["number", "payee", "description", "departure__reference"]
    sort_fields = ["date", "amount", "number"]
    default_sort = "-date"
    title = "Expenses & KRA"
    subtitle = "Staff and trip expenses with approval, and the monthly KRA obligations."
    placeholder = "Search expense, payee, trip…"
    export_name = "expenses"
    columns = [
        Col("Expense", "number", link=True, sub="payee"),
        Col("Date", "date", sort="date", kind="date"),
        Col("Category", "get_category_display", sub="description"),
        Col("Amount", "amount", sort="amount", kind="money"),
        Col("Receipt", "receipt_label", kind="pill", tone=lambda o: "success" if o.has_receipt else "danger"),
        Col("Status", "get_status_display", kind="pill", tone=lambda o: EXP_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Expense.objects.select_related("departure")
        self._unfiltered = qs
        val = self.request.GET.get("status", "")
        return qs.filter(status=val) if val else qs

    def actions(self):
        if not self.request.user.can_manage_finance:
            return []
        return [{"label": "New expense", "url": reverse("finance:expense_new"), "style": "accent", "icon": "plus"},
                {"label": "KRA obligations", "url": reverse("finance:kra"), "style": "secondary", "icon": "shield"}]

    def kpis(self, qs):
        month = qs.filter(date__gte=timezone.localdate().replace(day=1)).exclude(status="rejected")
        return [{"label": "This month", "value": f"KES {sum((e.amount for e in month), Decimal('0')):,.0f}", "accent": True},
                {"label": "Pending approval", "value": qs.filter(status="pending").count()},
                {"label": "Missing receipts", "value": qs.filter(receipt_file="").exclude(status="rejected").count(), "danger": True},
                {"label": "Approved, unpaid", "value": qs.filter(status="approved").count()}]




class ExpenseCreate(CreateScreen):
    module_key = FIN
    edit_permission = "can_manage_finance"
    model = Expense
    form_class = ExpenseForm
    title = "New expense"
    cancel_url_name = "finance:expenses"

    def form_valid(self, form):
        form.instance.submitted_by = self.request.user
        return super().form_valid(form)


@module_required(FIN)
def expense_detail(request, pk):
    e = get_object_or_404(Expense.objects.select_related("departure", "vehicle"), pk=pk)
    return render(request, "finance/expense_detail.html", {"e": e, "can_manage": request.user.can_manage_finance})


@require_POST
@module_required(FIN)
def expense_action(request, pk):
    _need_manage(request.user)
    e = get_object_or_404(Expense, pk=pk)
    a = request.POST.get("action")
    if a == "approve":
        _try(request, services.approve_expense, e, request.user, ok="Approved.")
    elif a == "reject":
        _try(request, services.approve_expense, e, request.user, False, ok="Rejected.")
    elif a == "pay":
        _try(request, services.pay_expense, e, request.user, ok="Marked paid and posted.")
    return redirect(e)


class KraList(TableScreen):
    module_key = FIN
    model = KraObligation
    tabs_field = "status"
    tabs_choices = KraObligation.STATUSES
    search_fields = ["basis", "payment_ref"]
    sort_fields = ["due_date", "amount"]
    default_sort = "due_date"
    title = "KRA Obligations"
    subtitle = "Rates and due days live in Tax settings. The accountant must confirm them before filing."
    export_name = "kra"
    columns = [
        Col("Tax", "get_tax_display", sub="basis"),
        Col("Period", "period", kind="date"),
        Col("Amount", "amount", kind="money"),
        Col("Due", "due_date", sort="due_date", kind="date"),
        Col("Status", "get_status_display", kind="pill", tone=lambda o: "danger" if o.is_overdue else KRA_TONE[o.status]),
        Col("Reference", "payment_ref"),
    ]

    def get_base_queryset(self):
        qs = KraObligation.objects.all()
        self._unfiltered = qs
        val = self.request.GET.get("status", "")
        return qs.filter(status=val) if val else qs

    def actions(self):
        if not self.request.user.can_manage_finance:
            return []
        return [{"label": "Tax settings", "url": reverse("finance:tax_settings"), "style": "secondary", "icon": "settings"}]

    def kpis(self, qs):
        due = qs.exclude(status="filed")
        today = timezone.localdate()
        return [{"label": "Unpaid obligations", "value": f"KES {sum((o.amount for o in due), Decimal('0')):,.0f}", "accent": True},
                {"label": "Overdue", "value": sum(1 for o in due if o.due_date < today), "danger": True},
                {"label": "Due in 14 days", "value": sum(1 for o in due if today <= o.due_date <= today + timedelta(days=14))},
                {"label": "Filed this year", "value": qs.filter(status="filed", period__year=today.year).count()}]


@require_POST
@module_required(FIN)
def kra_action(request, pk):
    _need_manage(request.user)
    ob = get_object_or_404(KraObligation, pk=pk)
    a = request.POST.get("action")
    if a == "filed":
        ob.status, ob.paid_at, ob.payment_ref = KraObligation.FILED, timezone.now(), request.POST.get("ref", "")[:60]
        ob.save()
        key = {"paye": "paye_payable", "ahl": "ahl_payable", "nssf": "nssf_payable", "shif": "shif_payable",
               "vat": "vat_output", "wht": "wht_payable", "corp": "tax_payable"}.get(ob.tax)
        if key and ob.amount:
            services.post(timezone.localdate(), f"Remitted {ob}", [(key, ob.amount, 0), ("bank", 0, ob.amount)],
                          ("kra", ob.pk, "pay"), request.user)
        messages.success(request, "Marked as filed and paid.")
    elif a == "refresh":
        services.refresh_obligations(ob.period)
        messages.success(request, "Recalculated.")
    return redirect("finance:kra")


@module_required(FIN)
def tax_settings(request):
    services.ensure_tax_settings()
    items = TaxSetting.objects.all()
    if request.method == "POST":
        _need_manage(request.user)
        t = get_object_or_404(TaxSetting, pk=request.POST.get("id"))
        f = TaxSettingForm(request.POST, instance=t)
        if f.is_valid():
            f.save()
            messages.success(request, f"{t.label} updated.")
        else:
            messages.error(request, _errors(f))
        return redirect("finance:tax_settings")
    return render(request, "finance/tax_settings.html", {"items": items, "can_manage": request.user.can_manage_finance})


# ================================================================================================ payroll
class PayrollList(TableScreen):
    module_key = FIN
    model = PayrollRun
    default_sort = "-period"
    sort_fields = ["period"]
    title = "Payroll"
    subtitle = "Prepare → Review → Approve → Pay & remit. PAYE, NSSF, SHIF and housing levy are calculated from Tax settings."
    export_name = "payroll"
    columns = [
        Col("Month", "period_label", link=True),
        Col("Staff", "staff_count", kind="num"),
        Col("Gross", "gross_total", kind="money"),
        Col("Net pay", "net_total", kind="money"),
        Col("Status", "get_status_display", kind="pill",
            tone=lambda o: {"prepare": "warning", "review": "info", "approved": "info", "paid": "success"}[o.status]),
    ]

    def get_base_queryset(self):
        return PayrollRun.objects.prefetch_related("lines")

    def actions(self):
        if not self.request.user.can_manage_finance:
            return []
        return [{"label": "Prepare a run", "url": reverse("finance:payroll_new"), "style": "accent", "icon": "plus"}]

    def kpis(self, qs):
        last = qs.first()
        t = last.totals() if last else {"gross": 0, "net": 0, "employees": 0, "paye": 0}
        return [{"label": "Latest run", "value": last.period.strftime("%b %Y") if last else "—", "accent": True},
                {"label": "Employees paid", "value": t["employees"]},
                {"label": "Gross payroll", "value": f"KES {t['gross']:,.0f}"},
                {"label": "PAYE", "value": f"KES {t['paye']:,.0f}"}]




@module_required(FIN)
def payroll_new(request):
    _need_manage(request.user)
    form = PayrollRunForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        run = _try(request, services.prepare_payroll, form.cleaned_data["month"], request.user, ok="Payroll prepared.")
        if run:
            return redirect(run)
    return render(request, "components/form_page.html", {
        "form": form, "title": "Prepare payroll", "subtitle": "Creates a line for every active employee with a basic salary; agents get commission on revenue won.",
        "cancel_url": reverse("finance:payroll")})


@module_required(FIN)
def payroll_run(request, pk):
    run = get_object_or_404(PayrollRun, pk=pk)
    lines = run.lines.select_related("employee")
    return render(request, "finance/payroll_run.html", {
        "run": run, "lines": lines, "t": run.totals(), "can_manage": request.user.can_manage_finance,
        "steps": [("prepare", "Prepare"), ("review", "Review"), ("approved", "Approve"), ("paid", "Pay & remit")],
        "current_index": ["prepare", "review", "approved", "paid"].index(run.status),
        "obligations": KraObligation.objects.filter(period=run.period), "line_form": PayrollLineForm()})


@require_POST
@module_required(FIN)
def payroll_action(request, pk):
    _need_manage(request.user)
    run = get_object_or_404(PayrollRun, pk=pk)
    a = request.POST.get("action")
    if a == "advance":
        _try(request, services.advance_payroll, run, request.user, ok="Payroll moved on.")
    elif a == "pay":
        _try(request, services.pay_payroll, run, request.user, request.POST.get("method", "bank"), ok="Salaries marked paid.")
    elif a == "recalc":
        _try(request, services.prepare_payroll, run.period, request.user, ok="Recalculated.")
    return redirect(run)


@require_POST
@module_required(FIN)
def payroll_line(request, pk):
    _need_manage(request.user)
    line = get_object_or_404(PayrollLine.objects.select_related("run"), pk=pk)
    if line.run.status not in (PayrollRun.PREPARE, PayrollRun.REVIEW):
        messages.error(request, "This run is locked.")
        return redirect(line.run)
    f = PayrollLineForm(request.POST)
    if f.is_valid():
        services.recalc_line(line, f.cleaned_data["allowances"], f.cleaned_data["other_deductions"])
        messages.success(request, f"{line.employee} updated.")
    else:
        messages.error(request, _errors(f))
    return redirect(line.run)


@module_required(FIN)
def payslip(request, pk):
    line = get_object_or_404(PayrollLine.objects.select_related("employee", "run"), pk=pk)
    ctx = {"l": line, "company": CompanySetting.load(), "print": request.GET.get("print") == "1"}
    if request.GET.get("pdf"):
        return render_pdf(request, "finance/payslip.html", ctx, f"payslip-{line.employee.initials}-{line.run.period:%Y-%m}.pdf",
                          f"{reverse('finance:payslip', args=[pk])}?print=1")
    return render(request, "finance/payslip.html", ctx)


# ================================================================================================ calendar
@module_required(FIN)
def calendar_view(request):
    today = timezone.localdate()
    try:
        y, m = int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month))
        first = date(y, m, 1)
    except ValueError:
        first = today.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    events = []
    for o in KraObligation.objects.filter(due_date__range=(first, last)):
        events.append((o.due_date, f"{o.get_tax_display()} due", "kra", o.status == "filed"))
    for b in Bill.objects.filter(due_date__range=(first, last)).exclude(status__in=["paid", "void"]):
        events.append((b.due_date, f"Bill {b.number} · {b.supplier.name[:18]}", "bill", False))
    for i in Invoice.objects.filter(due_date__range=(first, last), status__in=["sent", "part_paid"]):
        events.append((i.due_date, f"Invoice {i.number} due", "invoice", False))
    from apps.trips.models import Departure

    for d in Departure.objects.filter(start_date__range=(first, last)).exclude(stage="cancelled").select_related("package"):
        events.append((d.start_date, f"Departure {d.reference}", "trip", False))
    pay_day = min(int(services.rate("payroll_pay_day")), last.day)
    events.append((first.replace(day=pay_day), "Payroll pay day", "payroll", False))
    for e in FinanceEvent.objects.filter(date__range=(first, last)):
        events.append((e.date, e.title, e.category if e.category in ("review", "close") else "other", e.done))
    by_day = {}
    for d, title, cat, done in events:
        by_day.setdefault(d, []).append({"title": title, "cat": cat, "done": done})
    weeks = [[{"date": d, "in_month": d.month == first.month, "events": by_day.get(d, []), "today": d == today}
              for d in week] for week in calendar.Calendar(0).monthdatescalendar(first.year, first.month)]
    nxt = (last + timedelta(days=1))
    prev = (first - timedelta(days=1)).replace(day=1)
    soon = sorted((e for e in events if today <= e[0] <= today + timedelta(days=7)), key=lambda e: e[0])
    return render(request, "finance/calendar.html", {
        "weeks": weeks, "first": first, "prev": prev, "next": nxt, "soon": soon, "form": EventForm(),
        "can_manage": request.user.can_manage_finance})


@require_POST
@module_required(FIN)
def event_new(request):
    _need_manage(request.user)
    f = EventForm(request.POST)
    if f.is_valid():
        f.save()
        messages.success(request, "Event added.")
    else:
        messages.error(request, _errors(f))
    return redirect("finance:calendar")


# ================================================================================================ statements / budgets / reports
@module_required(FIN)
def statements(request):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    kind = request.GET.get("type", "income")
    ctx.update(kind=kind, tabs=[("income", "Income statement"), ("balance", "Balance sheet"), ("cash", "Cash flow")],
               pnl=services.income_statement(start, end), bs=services.balance_sheet(end),
               cf=services.cash_flow(start, end))
    return render(request, "finance/statements.html", ctx)


class BudgetList(TableScreen):
    module_key = FIN
    model = Budget
    default_sort = "-year"
    sort_fields = ["year", "amount"]
    title = "Budgets"
    subtitle = "Budget vs actual from the ledger. Over-budget lines raise an alert."
    columns = [Col("Category", "category_label"), Col("Period", "period_label"), Col("Budget", "amount", kind="money"),
               Col("Approved", "approved", kind="bool")]
    export_name = "budgets"

    def actions(self):
        return [{"label": "New budget line", "url": reverse("finance:budget_new"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_finance else []


@module_required(FIN)
def budgets(request):
    today = timezone.localdate()
    ptype = request.GET.get("type", "month")
    year = int(request.GET.get("year", today.year)) if request.GET.get("year", "").isdigit() else today.year
    idx = int(request.GET.get("index", today.month if ptype == "month" else (today.month - 1) // 3 + 1)) \
        if request.GET.get("index", "1").isdigit() else 1
    rows = services.budget_vs_actual(year, ptype, idx)
    return render(request, "finance/budgets.html", {
        "rows": rows, "ptype": ptype, "year": year, "index": idx, "can_manage": request.user.can_manage_finance,
        "over": [r for r in rows if r["status"] == "Over budget"], "total_budget": sum((r["budget"].amount for r in rows if not r["revenue"]), Decimal("0")),
        "total_actual": sum((r["actual"] for r in rows if not r["revenue"]), Decimal("0"))})


@module_required(FIN)
def budget_edit(request):
    _need_manage(request.user)
    form = BudgetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        Budget.objects.update_or_create(period_type=d["period_type"], year=d["year"],
                                        index=d["index"] if d["period_type"] != "year" else 1, category=d["category"],
                                        defaults={"amount": d["amount"], "approved": True})
        messages.success(request, "Budget saved.")
        return redirect("finance:budgets")
    return render(request, "components/form_page.html", {"form": form, "title": "Budget line",
                                                         "subtitle": "Same category and period updates the existing line.",
                                                         "cancel_url": reverse("finance:budgets")})


@module_required(FIN)
def reports_index(request):
    ctx = _ctx(request)
    ctx["reports"] = [(k, v[0], v[2]) for k, v in report_lib.REPORTS.items()]
    return render(request, "finance/reports.html", ctx)


@module_required(FIN)
def report_view(request, slug):
    if slug not in report_lib.REPORTS:
        raise Http404
    start, end, key = _range(request)
    title, headers, rows = report_lib.REPORTS[slug][1](start, end)
    fmt = request.GET.get("export")
    if fmt == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{slug}.csv"'
        w = csv.writer(resp)
        w.writerow(headers)
        w.writerows(rows)
        return resp
    if fmt == "xlsx":
        import io

        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = slug[:30]
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="4F1410")
        for r in rows:
            ws.append([str(c) if not isinstance(c, (int, float)) else c for c in r])
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{slug}.xlsx"'
        return resp
    return render(request, "finance/report.html", _ctx(request, title=title, headers=headers, slug=slug,
                                                      rows=[list(zip(headers, r)) for r in rows]))
