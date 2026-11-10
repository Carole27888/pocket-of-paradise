from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import module_required
from apps.contacts.models import Client
from apps.sales import services as sales
from apps.sales.models import SalesSettings

from . import services
from .models import BudgetRequest, ClientKYC, Instalment, PaymentPlan, SignatureRequest

DEALS = "deals"


def _can_manage(user):
    return user.is_superuser or user.has_role(R.ADMIN, R.COO, R.SALES_MANAGER, R.FINANCE)


# ------------------------------------------------------------------ overview
@module_required(DEALS, "sales")
def overview(request):
    st = SalesSettings.get()
    if request.method == "POST" and _can_manage(request.user):
        st.require_kyc_for_deposit = bool(request.POST.get("require_kyc"))
        st.save(update_fields=["require_kyc_for_deposit"])
        messages.success(request, "Setting saved.")
        return redirect("deals:overview")
    bookings = sales.bookings_for(request.user)
    plans = PaymentPlan.objects.filter(booking__in=bookings).select_related("booking__enquiry__client").prefetch_related("instalments")
    overdue = [i for i in Instalment.objects.filter(plan__booking__in=bookings, due_date__lt=timezone.localdate()).select_related("plan__booking__enquiry__client") if i.outstanding > 0]
    return render(request, "dealdesk/overview.html", {
        "st": st, "can_manage": _can_manage(request.user), "kyc_pending": ClientKYC.objects.filter(status="pending").count(),
        "sig_pending": SignatureRequest.objects.filter(status="pending").count(), "plans": plans[:10], "overdue": overdue[:10],
        "pending_budgets": BudgetRequest.objects.filter(status="pending").count()})


# ------------------------------------------------------------------ KYC
@module_required(DEALS, "sales")
def kyc_list(request):
    qs = ClientKYC.objects.select_related("client", "verified_by")
    status = request.GET.get("status") or ""
    if status:
        qs = qs.filter(status=status)
    return render(request, "dealdesk/kyc_list.html", {"items": qs, "status": status, "can_manage": _can_manage(request.user),
                                                      "unverified": sales_clients(request.user).filter(kyc__isnull=True)[:10]})


@module_required(DEALS, "sales")
def kyc_submit(request, client_id):
    client = get_object_or_404(sales_clients(request.user), pk=client_id)
    kyc = ClientKYC.objects.filter(client=client).first()
    is_modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    error = ""
    if request.method == "POST":
        doc = request.FILES.get("document")
        if doc and doc.size > 8 * 1024 * 1024:
            error = "That file is over 8 MB."
        elif not request.POST.get("id_number", "").strip():
            error = "Enter the ID or passport number."
        else:
            services.submit_kyc(client, id_type=request.POST.get("id_type", "national_id"), id_number=request.POST["id_number"].strip(), document=doc, user=request.user)
            messages.success(request, "KYC submitted for verification.")
            if is_modal:
                return JsonResponse({"ok": True})
            return redirect("deals:kyc")
        if not is_modal:
            messages.error(request, error)
    ctx = {"client": client, "kyc": kyc, "id_types": ClientKYC.ID_TYPES, "error": error, "posted": request.POST}
    if is_modal:   # opens as a pop-up; errors come back in the same fragment
        return render(request, "dealdesk/kyc_modal.html", ctx, status=422 if error else 200)
    return render(request, "dealdesk/kyc_form.html", ctx)


def sales_clients(user):
    from apps.contacts.views import visible_clients

    return visible_clients(user)


@require_POST
@module_required(DEALS, "sales")
def kyc_decide(request, pk):
    if not _can_manage(request.user):
        raise PermissionDenied
    kyc = get_object_or_404(ClientKYC, pk=pk)
    try:
        services.decide_kyc(kyc, request.user, request.POST.get("action") == "verify", request.POST.get("note", ""))
        messages.success(request, "KYC " + kyc.get_status_display().lower() + ".")
    except PermissionError as e:
        messages.error(request, str(e))
    return redirect("deals:kyc")


# ------------------------------------------------------------------ signatures
@module_required(DEALS, "sales")
def signatures(request):
    return render(request, "dealdesk/signatures.html", {"items": SignatureRequest.objects.select_related("client", "sent_by")[:100]})


@require_POST
@module_required(DEALS, "sales")
def signature_request(request, kind, pk):
    if kind == "booking":
        obj = get_object_or_404(sales.bookings_for(request.user), pk=pk)
        enquiry = obj.enquiry
    else:
        from apps.leads.views import visible_enquiries
        from apps.sales.models import Quotation

        obj = get_object_or_404(Quotation.objects.filter(enquiry__in=visible_enquiries(request.user)), pk=pk)
        enquiry = obj.enquiry
    if not sales.can_act_on(request.user, enquiry):
        raise PermissionDenied
    req = services.request_signature(request.user, **{"booking" if kind == "booking" else "quotation": obj})
    link = request.build_absolute_uri(f"/sign/{req.token}/")
    messages.success(request, f"Signing link ready: {link}")
    return redirect("deals:signatures")


def public_sign(request, token):
    """Public page (no login): the client reads the summary, types their name and draws a signature."""
    req = get_object_or_404(SignatureRequest.objects.select_related("client", "quotation", "booking"), token=token)
    obj = req.quotation or req.booking
    error = ""
    if request.method == "POST" and req.status == SignatureRequest.PENDING:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")) or None
        try:
            services.sign(req, name=request.POST.get("name", ""), image=request.POST.get("signature", ""), ip=ip, decline=request.POST.get("action") == "decline")
        except ValueError as e:
            error = str(e)
    return render(request, "dealdesk/public_sign.html", {"req": req, "obj": obj, "error": error})


# ------------------------------------------------------------------ payment plans
@module_required(DEALS, "sales")
def plan(request, booking_id):
    b = get_object_or_404(sales.bookings_for(request.user), pk=booking_id)
    can_act = sales.can_act_on(request.user, b.enquiry)
    if request.method == "POST":
        if not can_act:
            raise PermissionDenied
        try:
            from datetime import date

            services.create_plan(b, request.user, count=int(request.POST.get("count", 3)), first_due=date.fromisoformat(request.POST.get("first_due")),
                                 interval_days=int(request.POST.get("interval", 30)), note=request.POST.get("note", ""))
            messages.success(request, "Payment plan created.")
            return redirect("deals:plan", booking_id=b.pk)
        except (ValueError, TypeError) as e:
            messages.error(request, str(e) or "Check the dates and numbers.")
    return render(request, "dealdesk/plan.html", {"b": b, "plan": PaymentPlan.objects.filter(booking=b).prefetch_related("instalments").first(), "can_act": can_act,
                                                  "default_due": timezone.localdate() + timedelta(days=30)})


# ------------------------------------------------------------------ budget requests
@login_required
def budgets(request):
    deciders = request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO, R.DIRECTOR)
    if request.method == "POST":
        act = request.POST.get("action")
        try:
            if act == "new":
                services.request_budget(request.user, title=request.POST["title"], amount=Decimal(request.POST["amount"]),
                                        department=request.POST.get("department", ""), reason=request.POST.get("reason", ""))
                messages.success(request, "Request sent for approval.")
            elif act in ("approve", "reject") and deciders:
                services.decide_budget(get_object_or_404(BudgetRequest, pk=request.POST.get("pk")), request.user, act == "approve", request.POST.get("note", ""))
        except (ValueError, PermissionError, InvalidOperation, KeyError) as e:
            messages.error(request, str(e) or "Check the amount.")
        return redirect("deals:budgets")
    qs = BudgetRequest.objects.select_related("requested_by", "decided_by")
    if not deciders:
        qs = qs.filter(requested_by=request.user)
    return render(request, "dealdesk/budgets.html", {"items": qs[:100], "deciders": deciders})
