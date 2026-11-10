from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.money import format_money
from apps.core.tables import Col, TableScreen
from apps.finance.forms import BillPayForm

from .forms import BillForm, SupplierForm
from .models import Bill, Supplier

SUP = "suppliers"
BILL_TONE = {"awaiting": "warning", "approved": "info", "scheduled": "info", "paid": "success", "void": "danger"}


class SupplierList(TableScreen):
    module_key = SUP
    model = Supplier
    tabs_field = "kind"
    tabs_choices = Supplier.KINDS
    search_fields = ["name", "contact_name", "phone", "email", "location"]
    sort_fields = ["name", "kind"]
    default_sort = "name"
    title = "Suppliers"
    subtitle = "Lodges, camps, transporters, caterers, activity providers and park-fee agents."
    placeholder = "Search supplier, contact, location…"
    export_name = "suppliers"
    columns = [
        Col("Supplier", "name", sort="name", link=True, sub="location"),
        Col("Type", "get_kind_display"),
        Col("Contact", "contact_name", sub="phone"),
        Col("Terms", "terms_label"),
        Col("Outstanding", "outstanding", kind="money"),
    ]

    def get_base_queryset(self):
        qs = Supplier.objects.alive().prefetch_related("bills__payments")
        self._unfiltered = qs
        val = self.request.GET.get("kind", "")
        return qs.filter(kind=val) if val else qs

    def actions(self):
        return [{"label": "New supplier", "url": reverse("suppliers:create"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_suppliers else []

    def kpis(self, qs):
        bills = Bill.objects.exclude(status__in=["paid", "void"]).prefetch_related("payments")
        owed = sum((b.balance * b.fx_rate for b in bills), Decimal("0"))
        return [{"label": "Active suppliers", "value": qs.filter(is_active=True).count(), "accent": True},
                {"label": "We owe suppliers", "value": format_money(owed)},
                {"label": "Bills awaiting approval", "value": Bill.objects.filter(status="awaiting").count()},
                {"label": "Prepayments open", "value": Bill.objects.filter(prepayment=True).exclude(status__in=["void"]).count()}]


@module_required(SUP)
def supplier_detail(request, pk):
    s = get_object_or_404(Supplier.objects.alive(), pk=pk)
    bills = s.bills.select_related("departure").prefetch_related("payments")
    billed = sum((b.amount * b.fx_rate for b in bills.exclude(status="void")), Decimal("0"))
    return render(request, "suppliers/detail.html", {"s": s, "bills": bills[:30], "billed": billed,
                                                     "can_manage": request.user.can_manage_suppliers})


class SupplierCreate(CreateScreen):
    module_key = SUP
    edit_permission = "can_manage_suppliers"
    model = Supplier
    form_class = SupplierForm
    title = "New supplier"
    cancel_url_name = "suppliers:list"


class SupplierUpdate(UpdateScreen):
    module_key = SUP
    edit_permission = "can_manage_suppliers"
    model = Supplier
    form_class = SupplierForm

    def get_queryset(self):
        return Supplier.objects.alive()


class BillList(TableScreen):
    module_key = SUP
    model = Bill
    tabs_field = "status"
    tabs_choices = Bill.STATUSES
    search_fields = ["number", "supplier__name", "supplier_ref", "description", "departure__reference"]
    sort_fields = ["due_date", "amount", "bill_date"]
    default_sort = "due_date"
    title = "Bills & Prepayments"
    subtitle = "What we owe suppliers. Finance approves and pays; prepayments become trip costs when the departure completes."
    placeholder = "Search bill, supplier, departure…"
    export_name = "bills"
    columns = [
        Col("Bill", "number", link=True, sub="supplier"),
        Col("Departure", "departure", sub="get_category_display"),
        Col("Due", "due_date", sort="due_date", kind="date"),
        Col("Amount", "amount", sort="amount", kind="money", currency="currency"),
        Col("Status", "due_label", kind="pill", tone=lambda o: "danger" if o.is_overdue else BILL_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Bill.objects.select_related("supplier", "departure").prefetch_related("payments")
        self._unfiltered = qs
        val = self.request.GET.get("status", "")
        return qs.filter(status=val) if val else qs

    def actions(self):
        return [{"label": "New bill", "url": reverse("suppliers:bill_new"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_suppliers else []

    def kpis(self, qs):
        open_ = list(qs.exclude(status__in=["paid", "void"]))
        today = timezone.localdate()
        return [{"label": "Outstanding", "value": format_money(sum((b.balance * b.fx_rate for b in open_), Decimal("0"))), "accent": True},
                {"label": "Awaiting approval", "value": sum(1 for b in open_ if b.status == "awaiting")},
                {"label": "Due this week", "value": sum(1 for b in open_ if today <= b.due_date <= today + timedelta(days=7))},
                {"label": "Overdue", "value": sum(1 for b in open_ if b.due_date < today), "danger": True}]


@module_required(SUP)
def bill_new(request):
    if not request.user.can_manage_suppliers:
        raise PermissionDenied
    initial = {}
    if request.GET.get("supplier", "").isdigit():
        initial["supplier"] = int(request.GET["supplier"])
    if request.GET.get("departure", "").isdigit():
        initial["departure"] = int(request.GET["departure"])
    form = BillForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        bill = form.save(commit=False)
        bill.created_by = request.user
        bill.save()
        messages.success(request, f"{bill.number} created. It now waits for Finance approval.")
        return redirect(bill)
    return render(request, "components/form_page.html", {"form": form, "title": "New supplier bill",
                                                         "cancel_url": reverse("suppliers:bills")})


@module_required(SUP)
def bill_detail(request, pk):
    b = get_object_or_404(Bill.objects.select_related("supplier", "departure", "approved_by"), pk=pk)
    return render(request, "suppliers/bill.html", {
        "b": b, "payments": b.payments.select_related("recorded_by"), "can_finance": request.user.can_manage_finance,
        "pay_form": BillPayForm(initial={"amount": b.balance})})


class DepartureCosts(TableScreen):
    module_key = SUP
    title = "Departure Costs"
    subtitle = "What we still owe for each trip, grouped by departure (Operations: what's left to prepay before it leaves)."
    export_name = "departure-costs"
    sort_fields = ["start_date"]
    default_sort = "start_date"
    columns = [
        Col("Departure", "reference", link=True, sub="package.title"),
        Col("Leaves", "start_date", sort="start_date", kind="date"),
        Col("Bills", "bill_count", kind="num"),
        Col("Billed", "billed_total", kind="money"),
        Col("Still owed", "owed_total", kind="money"),
        Col("Prepaid", "prepay_label", kind="pill", tone=lambda o: "success" if o.prepay_label == "Yes" else "warning"),
    ]

    @property
    def model(self):
        from apps.trips.models import Departure

        return Departure

    def get_base_queryset(self):
        from apps.trips.models import Departure

        return (Departure.objects.exclude(stage="cancelled").select_related("package")
                .prefetch_related("bills__payments").filter(start_date__gte=timezone.localdate() - timedelta(days=30)))

    def kpis(self, qs):
        bills = Bill.objects.exclude(status__in=["paid", "void"]).exclude(departure__isnull=True).prefetch_related("payments")
        return [{"label": "Departures with open bills", "value": len({b.departure_id for b in bills}), "accent": True},
                {"label": "Owed on upcoming trips", "value": format_money(sum((b.balance * b.fx_rate for b in bills), Decimal("0")))}]
