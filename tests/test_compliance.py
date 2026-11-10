from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.compliance import services
from apps.compliance.models import DataBreach, DataRequest
from apps.contacts.models import Client
from apps.leads import services as lead_services
from apps.notifications.models import OutboundMessage
from apps.system.models import AuditLog


def _client_with_footprint(agent):
    from apps.catalog.models import TourPackage
    from apps.sales import services as sales
    from apps.trips.models import Departure, ManifestEntry

    pkg = TourPackage.objects.create(title="Mara", days=2, nights=1, currency="KES", price_per_person=1000, status="published")
    e = lead_services.create_enquiry(client_data={"full_name": "Wanjiru Kamau", "phone": "0712345678", "email": "w@example.com", "passport_number": "A1234567"},
                                     enquiry_data={"source": "call", "package": pkg, "adults": 1, "notes": "Vegetarian, anniversary"}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    q = sales.create_quotation(e, agent.user, currency="KES", items=[{"kind": "other", "description": "T", "quantity": 1, "unit_price": 1000}])
    sales.send_quotation(q, "email", agent.user)
    b = sales.accept_quotation(q, agent.user)
    dep = Departure.objects.create(package=pkg, start_date=timezone.localdate(), end_date=timezone.localdate())
    ManifestEntry.objects.create(departure=dep, booking=b, full_name="Wanjiru Kamau", passport_number="A1234567", phone="+254712345678", medical_notes="Asthma")
    OutboundMessage.objects.create(channel="whatsapp", to="+254712345678", body="Hi Wanjiru", status="sent")
    return e.client, b


@pytest.mark.django_db
def test_due_date_defaults_to_30_days_and_references_run():
    r = DataRequest.objects.create(kind="access", requester_name="A", received_on=timezone.localdate())
    assert r.reference == "DSR-0001" and r.due_on == timezone.localdate() + timedelta(days=30)
    assert not r.is_overdue
    DataRequest.objects.filter(pk=r.pk).update(due_on=timezone.localdate() - timedelta(days=1))
    r.refresh_from_db()
    assert r.is_overdue


@pytest.mark.django_db
def test_cannot_close_without_a_response():
    r = DataRequest.objects.create(kind="access", requester_name="A")
    with pytest.raises(ValueError, match="response"):
        services.close_request(r, "   ")
    services.close_request(r, "Sent a copy of your data by e-mail.")
    assert r.status == "closed" and r.closed_at
    with pytest.raises(ValueError, match="already closed"):
        services.close_request(r, "again")


@pytest.mark.django_db
def test_anonymise_guards_then_scrubs_but_keeps_the_books(agent):
    client, booking = _client_with_footprint(agent)
    access = DataRequest.objects.create(kind="access", requester_name="W", client=client, identity_confirmed=True)
    with pytest.raises(ValueError, match="deletion"):
        services.anonymise(access)
    req = DataRequest.objects.create(kind="deletion", requester_name="Wanjiru")
    with pytest.raises(ValueError, match="Link the request"):
        services.anonymise(req)
    req.client = client
    req.save()
    with pytest.raises(ValueError, match="identity"):
        services.anonymise(req)
    req.identity_confirmed = True
    req.save()
    counts = services.anonymise(req)
    client.refresh_from_db(); booking.refresh_from_db()
    assert client.full_name == f"Anonymised guest #{client.pk}" and client.phone == "" and client.email == "" and client.passport_number == ""
    assert client.passport_hash == ""                                              # the searchable hash is wiped too
    assert booking.invoices.exists() and booking.total_amount == 1160 and booking.client_id == client.pk      # the books survive
    entry = booking.manifest_entries.get()
    assert entry.full_name == "Anonymised guest" and entry.passport_number == "" and entry.medical_notes == "" and entry.phone == ""
    assert booking.enquiry.notes == ""
    msg = OutboundMessage.objects.get()
    assert msg.to == "[removed]" and msg.body == "[removed]"
    assert counts["client"] == 1 and AuditLog.objects.filter(action="client.anonymised").exists()
    with pytest.raises(ValueError, match="already been anonymised"):
        services.anonymise(req)


@pytest.mark.django_db
def test_retention_removes_old_sensitive_data_and_is_repeatable(agent):
    from apps.leads.models import Stage
    from apps.marketing.models import Channel, InboxMessage
    from apps.marketing import services as mkt
    from apps.trips.models import ManifestEntry

    client, booking = _client_with_footprint(agent)
    entry = booking.manifest_entries.get()
    dep = entry.departure
    type(dep).objects.filter(pk=dep.pk).update(stage="completed", completed_at=timezone.now() - timedelta(days=120))
    mkt.ensure_channels()
    ch = Channel.objects.first()
    old = InboxMessage.objects.create(channel=ch, sender="X", message="hi", received_at=timezone.now() - timedelta(days=400), external_id="o")
    new = InboxMessage.objects.create(channel=ch, sender="Y", message="hi", external_id="n")
    OutboundMessage.objects.filter().update(created_at=timezone.now() - timedelta(days=400))
    counts = services.run_retention()
    entry.refresh_from_db()
    assert entry.passport_number == "" and entry.medical_notes == "" and counts["manifest_sensitive"] == 1
    assert not InboxMessage.objects.filter(pk=old.pk).exists() and InboxMessage.objects.filter(pk=new.pk).exists()
    assert OutboundMessage.objects.get().body == "[removed]"
    assert services.run_retention()["manifest_sensitive"] == 0                      # nothing left to do


@pytest.mark.django_db
def test_lost_enquiry_guest_is_scrubbed_only_when_nothing_else_is_on_file(agent):
    from apps.leads.models import Enquiry, Stage

    lost = lead_services.create_enquiry(client_data={"full_name": "Cold Lead", "phone": "0722000001"}, enquiry_data={"source": "call", "notes": "x"}, auto_assign=False)
    keep = lead_services.create_enquiry(client_data={"full_name": "Has Invoice", "phone": "0722000002"}, enquiry_data={"source": "call"}, auto_assign=False)
    old = timezone.now() - timedelta(days=800)
    Enquiry.objects.filter(pk__in=[lost.pk, keep.pk]).update(stage=Stage.LOST, stage_changed_at=old)
    from apps.finance import services as fin

    fin.issue_invoice(fin.create_invoice(keep.client, lines=[{"description": "x", "quantity": 1, "unit_price": 100}]))
    assert services.run_retention()["lost_enquiries"] == 1
    lost.client.refresh_from_db(); keep.client.refresh_from_db()
    assert lost.client.full_name.startswith("Anonymised guest") and keep.client.full_name == "Has Invoice"


@pytest.mark.django_db
def test_breach_deadlines_and_status():
    b = DataBreach.objects.create(title="Laptop lost", what_happened="x", data_affected="names", severity="high", people_affected=12,
                                  detected_at=timezone.now() - timedelta(hours=80))
    assert b.reference == "BR-001" and b.report_overdue and b.status == "Open"
    b.contained_at, b.reported_at, b.notified_at = timezone.now(), timezone.now(), timezone.now()
    b.save()
    assert b.status == "Closed" and not b.report_overdue
    low = DataBreach.objects.create(title="Typo e-mail", what_happened="x", data_affected="1 name", severity="low", contained_at=timezone.now())
    assert not low.report_overdue and low.status == "Closed"


@pytest.mark.django_db
def test_screens_roles_and_closing_flow(client, make_user, agent):
    cl, _ = _client_with_footprint(agent)
    admin, ceo, fin = make_user("adm@example.com", R.ADMIN), make_user("ceo@example.com", R.DIRECTOR), make_user("fin@example.com", R.FINANCE)
    client.force_login(fin)
    assert client.get("/compliance/").status_code == 403
    client.force_login(admin)
    r = client.post("/compliance/requests/new/", {"kind": "deletion", "requester_name": "Wanjiru", "received_on": timezone.localdate().isoformat(), "client": cl.pk})
    assert r.status_code == 302
    req = DataRequest.objects.get()
    for u in ("/compliance/", "/compliance/requests/", f"/compliance/requests/{req.pk}/", "/compliance/breaches/", "/compliance/breaches/new/", "/compliance/retention/", "/privacy/"):
        assert client.get(u).status_code == 200, u
    act = reverse("compliance:request_action", args=[req.pk])
    client.post(act, {"action": "anonymise"})                                       # blocked: identity not confirmed
    cl.refresh_from_db()
    assert cl.full_name == "Wanjiru Kamau"
    client.post(act, {"action": "identity", "note": "Phone + passport last 3"})
    client.post(act, {"action": "close", "response": ""})
    req.refresh_from_db()
    assert req.status != "closed"
    client.post(act, {"action": "anonymise"})
    cl.refresh_from_db()
    assert cl.full_name.startswith("Anonymised")
    client.post(act, {"action": "close", "response": "Your data was erased on request."})
    req.refresh_from_db()
    assert req.status == "closed"
    client.force_login(ceo)                                                          # the CEO reads but cannot act
    assert client.get("/compliance/").status_code == 200
    assert client.post(act, {"action": "progress"}).status_code == 403
    assert client.post("/compliance/retention/").status_code == 403


@pytest.mark.django_db
def test_public_privacy_page_needs_no_login(client):
    r = client.get("/privacy/")
    assert r.status_code == 200 and b"Your rights" in r.content
