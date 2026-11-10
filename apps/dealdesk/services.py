"""KYC gate, e-signatures, payment-plan instalments and budget requests."""
import hashlib
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts import roles as R
from apps.notifications.services import notify, notify_roles
from apps.system.services import audit

from .models import BudgetRequest, ClientKYC, Instalment, PaymentPlan, SignatureRequest

D = Decimal
SIGN_VALID_DAYS = 14


# ------------------------------------------------------------------ KYC
def kyc_ok(client) -> bool:
    k = ClientKYC.objects.filter(client=client).first()
    return bool(k and k.status == ClientKYC.VERIFIED)


def check_deposit_allowed(booking):
    """Raise ValueError when the KYC-before-deposit rule is on and the client isn't verified (only the first payment is gated)."""
    from apps.sales.models import SalesSettings

    if SalesSettings.get().require_kyc_for_deposit and booking.amount_paid <= 0 and not kyc_ok(booking.enquiry.client):
        raise ValueError("This client's identity hasn't been verified yet. Complete KYC before taking a deposit.")


@transaction.atomic
def submit_kyc(client, *, id_type, id_number, document=None, user=None) -> ClientKYC:
    k, _ = ClientKYC.objects.get_or_create(client=client)
    k.id_type, k.id_number = id_type, id_number
    if document:
        k.document = document
    k.status, k.verified_by, k.verified_at = ClientKYC.PENDING, None, None
    k.save()
    notify_roles({R.ADMIN, R.COO, R.SALES_MANAGER, R.FINANCE}, f"KYC to verify: {client.full_name}", "", "/deals/kyc/", "info", dedupe_key=f"kyc:{k.pk}:{k.updated_at:%Y%m%d%H%M}")
    return k


@transaction.atomic
def decide_kyc(kyc: ClientKYC, user, verify: bool, note="") -> ClientKYC:
    if kyc.client.owner_id and kyc.client.owner.user_id == user.pk and not user.has_role(*R.ALL_ACCESS):
        raise PermissionError("Someone other than the client's own agent must verify the identity.")
    kyc.status = ClientKYC.VERIFIED if verify else ClientKYC.REJECTED
    kyc.verified_by, kyc.verified_at, kyc.note = user, timezone.now(), note[:240]
    kyc.save()
    audit(user, "kyc_" + kyc.status, kyc, label=str(kyc.client))
    return kyc


# ------------------------------------------------------------------ e-signature
def document_text(obj) -> str:
    return f"{obj.reference}|{getattr(obj, 'title', '')}|{getattr(obj, 'total', '') or getattr(obj, 'total_amount', '')}|{getattr(obj, 'currency', '')}"


def request_signature(user, *, quotation=None, booking=None) -> SignatureRequest:
    obj = quotation or booking
    if obj is None:
        raise ValueError("Choose a quotation or booking to sign.")
    client = (quotation.enquiry.client if quotation else booking.enquiry.client)
    title = f"{'Quotation' if quotation else 'Booking'} {obj.reference}"
    SignatureRequest.objects.filter(quotation=quotation, booking=booking, status=SignatureRequest.PENDING).update(status=SignatureRequest.DECLINED)
    return SignatureRequest.objects.create(title=title, quotation=quotation, booking=booking, client=client, sent_by=user,
                                           content_hash=hashlib.sha256(document_text(obj).encode()).hexdigest(),
                                           expires_at=timezone.now() + timedelta(days=SIGN_VALID_DAYS))


@transaction.atomic
def sign(req: SignatureRequest, *, name: str, image: str, ip: str | None, decline=False) -> SignatureRequest:
    if req.status != SignatureRequest.PENDING:
        raise ValueError("This document has already been handled.")
    if req.expired:
        raise ValueError("This signing link has expired. Ask your consultant to send a new one.")
    if decline:
        req.status = SignatureRequest.DECLINED
    else:
        if len(name.strip()) < 3:
            raise ValueError("Type your full name to sign.")
        if not image.startswith("data:image/png;base64,") or len(image) < 200:
            raise ValueError("Please draw your signature in the box.")
        if len(image) > 400_000:
            raise ValueError("That signature image is too large.")
        req.status, req.signed_name, req.signature_image, req.signed_at, req.signed_ip = SignatureRequest.SIGNED, name.strip(), image, timezone.now(), ip
    req.save()
    if req.sent_by_id:
        notify(req.sent_by, f"{req.title} {'signed' if not decline else 'declined'} by {req.client.full_name}", "", "/deals/signatures/", "success" if not decline else "warning")
    return req


# ------------------------------------------------------------------ payment plans
@transaction.atomic
def create_plan(booking, user, *, count: int, first_due, interval_days=30, note="") -> PaymentPlan:
    """Split the unpaid balance into equal instalments (the last one absorbs rounding)."""
    if count < 1 or count > 24:
        raise ValueError("Choose between 1 and 24 instalments.")
    balance = booking.balance
    if balance <= 0:
        raise ValueError("This booking is already paid in full.")
    if hasattr(booking, "plan"):
        booking.plan.delete()
    plan = PaymentPlan.objects.create(booking=booking, created_by=user, note=note)
    each = (balance / count).quantize(D("0.01"))
    for i in range(count):
        amt = each if i < count - 1 else balance - each * (count - 1)
        Instalment.objects.create(plan=plan, due_date=first_due + timedelta(days=interval_days * i), amount=amt)
    return plan


def apply_payment_to_plan(booking, amount: D):
    """Payments settle the oldest instalment first; called after a payment is recorded."""
    plan = PaymentPlan.objects.filter(booking=booking).first()
    if not plan:
        return
    left = D(amount)
    for ins in plan.instalments.order_by("due_date", "pk"):
        if left <= 0:
            break
        take = min(ins.outstanding, left)
        if take > 0:
            ins.paid_amount += take
            ins.save(update_fields=["paid_amount"])
            left -= take


# ------------------------------------------------------------------ budget requests
def request_budget(user, *, title, amount, department="", reason="") -> BudgetRequest:
    br = BudgetRequest.objects.create(title=title, amount=amount, department=department, reason=reason, requested_by=user)
    notify_roles({R.ADMIN, R.COO, R.DIRECTOR}, f"Budget request {br.reference}: {title}", f"KES {amount:,.0f} from {user.full_name}", "/deals/budgets/", "info", dedupe_key=f"br:{br.pk}")
    return br


def decide_budget(br: BudgetRequest, user, approve: bool, note="") -> BudgetRequest:
    if br.status != BudgetRequest.PENDING:
        raise ValueError("Already decided.")
    if br.requested_by_id == user.pk and not user.has_role(R.DIRECTOR):
        raise PermissionError("You can't approve your own request.")
    br.status = BudgetRequest.APPROVED if approve else BudgetRequest.REJECTED
    br.decided_by, br.decided_at, br.decision_note = user, timezone.now(), note[:240]
    br.save()
    notify(br.requested_by, f"Budget {br.reference} {br.status}", br.title, "/deals/budgets/", "success" if approve else "warning", dedupe_key=f"brd:{br.pk}")
    audit(user, "budget_" + br.status, br, label=br.reference)
    return br
