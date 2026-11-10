"""Enquiry business logic shared by Front Desk, Sales and Marketing."""
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.contacts.models import Client
from apps.core.phone import normalise_phone

from .models import Enquiry, LeadActivity, LeadAssignment, Stage


@dataclass
class ClientMatch:
    client: Client | None
    returning: bool
    last_enquiry: Enquiry | None = None


def find_existing_client(phone: str = "", email: str = "", passport: str = "") -> Client | None:
    import hashlib

    q = Q()
    p = normalise_phone(phone)
    if p:
        q |= Q(phone=p) | Q(alt_phone=p)
    if email:
        q |= Q(email__iexact=email.strip())
    if passport:
        h = hashlib.sha256(passport.replace(" ", "").upper().encode()).hexdigest()
        q |= Q(passport_hash=h)
    if not q:
        return None
    return Client.objects.alive().filter(q).order_by("created_at").first()


def match_client(phone="", email="", passport="") -> ClientMatch:
    client = find_existing_client(phone, email, passport)
    if not client:
        return ClientMatch(None, False)
    last = client.enquiries.select_related("assigned_agent", "package", "destination").order_by("-created_at").first()
    return ClientMatch(client, True, last)


def log_activity(enquiry: Enquiry, activity_type: str, details: str = "", user=None, when=None) -> LeadActivity:
    act = LeadActivity.objects.create(
        enquiry=enquiry, activity_type=activity_type, details=details, user=user, timestamp=when or timezone.now()
    )
    if activity_type in LeadActivity.CONTACT_TYPES:
        Enquiry.objects.filter(pk=enquiry.pk).update(last_contact_at=act.timestamp)
        enquiry.last_contact_at = act.timestamp
    return act


@transaction.atomic
def create_enquiry(*, client_data: dict, enquiry_data: dict, user=None, auto_assign: bool = True) -> Enquiry:
    """Create an enquiry, linking to an existing client when phone/email/passport already exist."""
    match = match_client(client_data.get("phone", ""), client_data.get("email", ""),
                         client_data.get("passport_number", ""))
    if match.client:
        client = match.client
        changed = False
        for f in ("email", "nationality"):
            if client_data.get(f) and not getattr(client, f):
                setattr(client, f, client_data[f])
                changed = True
        if changed:
            client.save()
    else:
        client = Client.objects.create(**{k: v for k, v in client_data.items() if v not in (None,)})
    enquiry = Enquiry.objects.create(client=client, captured_by=user, is_returning_client=match.returning,
                                     **enquiry_data)
    log_activity(enquiry, "created", f"Captured via {enquiry.get_source_display()}", user)
    if auto_assign:
        from apps.sales.services import maybe_auto_assign

        transaction.on_commit(lambda: maybe_auto_assign(enquiry, user))
    return enquiry


@transaction.atomic
def change_stage(enquiry: Enquiry, stage: str, user=None, lost_reason: str = "") -> Enquiry:
    if stage not in Stage.values:
        raise ValueError(f"Unknown stage {stage}")
    if stage == enquiry.stage:
        return enquiry
    if stage == Stage.LOST and not lost_reason:
        raise ValueError("A lost reason is required")
    old = enquiry.get_stage_display()
    enquiry.stage = stage
    enquiry.stage_changed_at = timezone.now()
    enquiry.lost_reason = lost_reason if stage == Stage.LOST else ""
    enquiry.save(update_fields=["stage", "stage_changed_at", "lost_reason", "updated_at", "updated_by"])
    log_activity(enquiry, "stage", f"{old} → {enquiry.get_stage_display()}" + (f" ({lost_reason})" if lost_reason else ""), user)
    return enquiry


@transaction.atomic
def assign_agent(enquiry: Enquiry, agent, user=None, method: str = "manual", reason: str = "Reassigned") -> LeadAssignment:
    if not agent.is_available:
        raise ValueError(f"{agent} is not available ({agent.get_on_duty_display()})")
    now = timezone.now()
    enquiry.assignments.filter(active=True).exclude(agent=agent).update(active=False, ended_at=now, end_reason=reason)
    existing = enquiry.assignments.filter(active=True, agent=agent).first()
    if existing:
        return existing
    la = LeadAssignment.objects.create(enquiry=enquiry, agent=agent, assigned_by=user, method=method)
    enquiry.assigned_agent = agent
    enquiry.save(update_fields=["assigned_agent", "updated_at", "updated_by"])
    log_activity(enquiry, "assignment", f"Assigned to {agent} ({la.get_method_display()})", user)
    if not enquiry.client.owner_id:  # first agent on a client becomes their owner
        from apps.sales.services import set_client_owner

        set_client_owner(enquiry.client, agent, user, tell=False)
    if agent.user_id and agent.user_id != getattr(user, "pk", None):
        from apps.notifications.services import notify

        transaction.on_commit(lambda: notify(
            agent.user, f"New enquiry {enquiry.reference}",
            f"{enquiry.client.full_name} · {enquiry.interest_label} · {enquiry.get_source_display()}",
            enquiry.get_absolute_url(), "info", dedupe_key=f"assign:{la.pk}", purpose="lead_assigned",
            fields={"title": f"{enquiry.reference} · {enquiry.interest_label}", "body": f"{enquiry.client.full_name} {enquiry.client.phone or ''}".strip()}))
    return la
