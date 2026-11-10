"""Data-subject requests, anonymisation and the retention policy."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.system.services import audit

from .models import DataRequest, RetentionRun

# How long each kind of personal data is kept (days). Financial records are never deleted: only the person is scrubbed.
RETENTION = {
    "manifest_sensitive": ("Passport, medical and emergency details on manifests", 90, "after the trip completes"),
    "inbox_messages": ("Social inbox messages", 365, "after they arrive"),
    "outbound_messages": ("Text of sent SMS / WhatsApp / e-mail", 365, "after sending"),
    "lost_enquiries": ("Lost enquiries with no booking: notes, and the guest if nothing else is on file", 730, "after the enquiry was lost"),
}


def _scrub_client(client) -> dict:
    """Remove personal data from a client but keep the row (invoices and bookings must still point at someone)."""
    from apps.contacts.models import TravelPartyMember
    from apps.leads.models import Enquiry
    from apps.marketing.models import InboxMessage
    from apps.notifications.models import OutboundMessage
    from apps.trips.models import ManifestEntry

    phones = {p for p in (client.phone, client.alt_phone) if p}
    emails = {client.email} if client.email else set()
    name = client.full_name
    counts = {}
    counts["manifest"] = ManifestEntry.objects.filter(booking__client=client).update(
        full_name="Anonymised guest", passport_number="", phone="", dietary_notes="", medical_notes="", emergency_contact="", nationality="")
    counts["party"] = TravelPartyMember.objects.filter(enquiry__client=client).update(full_name="Anonymised guest", passport_number="", dietary_notes="", age=None)
    counts["enquiries"] = Enquiry.objects.filter(client=client).update(notes="", destination_text="")
    counts["inbox"] = InboxMessage.objects.filter(enquiry__client=client).update(sender="Anonymised", phone="", message="[removed]", reply_text="")
    if phones | emails:
        counts["messages"] = OutboundMessage.objects.filter(to__in=phones | emails).update(to="[removed]", body="[removed]", subject="")
    client.full_name = f"Anonymised guest #{client.pk}"
    client.phone = client.alt_phone = client.email = ""
    client.passport_number = ""
    client.country_of_residence = client.dietary_notes = client.medical_notes = client.notes = ""
    client.nationality = ""
    client.save()
    counts["client"] = 1
    return counts | {"was": name}


def close_request(req: DataRequest, response: str, user=None) -> DataRequest:
    if req.status == DataRequest.CLOSED:
        raise ValueError("This request is already closed.")
    if not (response or "").strip():
        raise ValueError("Record the response you gave the person before closing the request.")
    req.response, req.status, req.closed_at, req.closed_by = response.strip(), DataRequest.CLOSED, timezone.now(), user
    req.save()
    audit(user, "dsr.closed", req, f"{req.get_kind_display()} closed")
    return req


@transaction.atomic
def anonymise(req: DataRequest, user=None, request=None) -> dict:
    """Erasure: blocked unless this is a deletion request, linked to a client record, with identity confirmed."""
    if req.kind != DataRequest.DELETION:
        raise ValueError("Only a deletion / erasure request can anonymise a record.")
    if req.client_id is None:
        raise ValueError("Link the request to the person's client record first.")
    if not req.identity_confirmed:
        raise ValueError("Confirm the requester's identity before anonymising.")
    if req.anonymised_at:
        raise ValueError("This client has already been anonymised.")
    counts = _scrub_client(req.client)
    req.anonymised_at = timezone.now()
    if req.status == DataRequest.OPEN:
        req.status = DataRequest.PROGRESS
    req.save()
    audit(user, "client.anonymised", req.client, f"Erasure request {req.reference}. Scrubbed: " + ", ".join(f"{k}={v}" for k, v in counts.items() if k != "was"), request)
    return counts


def run_retention(user=None) -> dict:
    """Enforce the retention table. Safe to run repeatedly."""
    from apps.contacts.models import Client
    from apps.leads.models import Enquiry, Stage
    from apps.marketing.models import InboxMessage
    from apps.notifications.models import OutboundMessage
    from apps.trips.models import ManifestEntry

    now = timezone.now()
    counts = {}
    cut = now - timedelta(days=RETENTION["manifest_sensitive"][1])
    counts["manifest_sensitive"] = ManifestEntry.objects.filter(departure__completed_at__lt=cut).exclude(
        passport_number="", medical_notes="", emergency_contact="", phone="").update(passport_number="", medical_notes="", emergency_contact="", phone="")
    counts["inbox_messages"] = InboxMessage.objects.filter(received_at__lt=now - timedelta(days=RETENTION["inbox_messages"][1])).delete()[0]
    counts["outbound_messages"] = OutboundMessage.objects.filter(created_at__lt=now - timedelta(days=RETENTION["outbound_messages"][1])).exclude(body="[removed]").update(body="[removed]", subject="")
    lost_cut = now - timedelta(days=RETENTION["lost_enquiries"][1])
    n = 0
    for c in Client.objects.alive().filter(enquiries__stage=Stage.LOST, enquiries__stage_changed_at__lt=lost_cut).distinct():
        if c.enquiries.exclude(stage=Stage.LOST).exists() or c.bookings.exists() or c.invoices.exists() or c.full_name.startswith("Anonymised guest"):
            continue
        _scrub_client(c)
        n += 1
    counts["lost_enquiries"] = n
    RetentionRun.objects.create(by=user, counts=counts)
    audit(user, "retention.run", label="Retention policy", detail=", ".join(f"{k}: {v}" for k, v in counts.items()))
    return counts
