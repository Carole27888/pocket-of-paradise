import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.contacts.models import Client
from apps.frontdesk.models import BusinessCard
from apps.ict import bulk
from apps.ict.models import Broadcast, BroadcastRecipient, OptOut
from apps.system.services import save_credentials

pytestmark = pytest.mark.django_db


@pytest.fixture
def audience(db):
    Client.objects.create(full_name="Amina Said", phone="0712000001", email="amina@example.com")
    Client.objects.create(full_name="Brian Otieno", phone="0712000002", email="brian@example.com", marketing_opt_out=True)
    Client.objects.create(full_name="No Contact", phone="", email="")
    BusinessCard.objects.create(name="Carol Lodge", phone="0722000003", email="carol@lodge.co.ke", category="supplier", company="Mara Lodge")
    BusinessCard.objects.create(name="Dup", phone="0712000001", email="amina@example.com", category="partner")   # same person as a client


def test_business_cards_pages_and_modal(client, make_user):
    desk = make_user("desk@example.com", R.FRONTDESK_MANAGER)
    client.force_login(desk)
    assert "Business Cards" in client.get("/frontdesk/").content.decode()
    page = client.get(reverse("frontdesk:cards")).content.decode()
    assert 'data-modal-form="/frontdesk/cards/new/"' in page
    frag = client.get("/frontdesk/cards/new/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert frag.status_code == 200 and b"data-modal-form-body" in frag.content and b"<html" not in frag.content
    bad = client.post("/frontdesk/cards/new/", {"name": "No Contact", "category": "other"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert bad.status_code == 422
    ok = client.post("/frontdesk/cards/new/", {"name": "Jane Doe", "email": "Jane@Example.com", "phone": "0733 111 222", "category": "tour_operator", "company": "Safari Co"},
                     HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    c = BusinessCard.objects.get(name="Jane Doe")
    assert ok.json()["ok"] and c.email == "jane@example.com" and c.phone == "+254733111222" and c.added_by == desk
    assert "Safari Co" in client.get(reverse("frontdesk:cards"), {"category": "tour_operator"}).content.decode()
    assert client.get(reverse("frontdesk:card_edit", args=[c.pk])).status_code == 200
    assert client.post(reverse("frontdesk:card_delete", args=[c.pk])).status_code == 302 and not BusinessCard.objects.filter(pk=c.pk).exists()


def test_audience_dedupes_skips_opt_outs_and_bad_addresses(audience):
    spec = {"clients": {"enabled": True}, "cards": {"enabled": True}, "manual": "0799000001\njunk\nnew@example.com"}
    sms, skipped = bulk.build_audience(spec, "sms")
    nums = sorted(r["phone"] for r in sms)
    assert nums == ["+254712000001", "+254722000003", "+254799000001"]                       # deduped; opted-out Brian and no-number rows excluded
    reasons = {why for _n, why in skipped}
    assert "opted out" in reasons and "no valid phone number" in reasons
    mail, _ = bulk.build_audience(spec, "email")
    assert sorted(r["email"] for r in mail) == ["amina@example.com", "carol@lodge.co.ke", "new@example.com"]
    only_cards, _ = bulk.build_audience({"cards": {"enabled": True, "categories": ["supplier"]}}, "email")
    assert [r["name"] for r in only_cards] == ["Carol Lodge"]
    OptOut.objects.create(kind="email", value="carol@lodge.co.ke")
    assert "carol@lodge.co.ke" not in [r["email"] for r in bulk.build_audience({"cards": {"enabled": True}}, "email")[0]]


def test_merge_and_segments():
    assert bulk.merge("Hi {first_name}, {name}!", {"name": "Amina Said"}) == "Hi Amina, Amina Said!"
    assert bulk.merge("Hi {first_name}", {"name": ""}) == "Hi there"
    assert bulk.sms_segments("x" * 160) == 1 and bulk.sms_segments("x" * 161) == 2 and bulk.sms_segments("x" * 307) == 3


def test_mock_sms_and_email_flow_records_everything(audience):
    b = Broadcast.objects.create(channel="sms", name="Offer", body="Hi {first_name}, July offer", audience={"clients": {"enabled": True}, "cards": {"enabled": True}})
    assert bulk.start(b) == 2 and b.live is False and b.status == "sending"
    c = bulk.process(b)
    b.refresh_from_db()
    assert c["sent"] == 2 and c["failed"] == 0 and b.status == "sent" and b.finished_at
    assert BroadcastRecipient.objects.filter(broadcast=b, status="skipped").exists()
    e = Broadcast.objects.create(channel="email", name="News", subject="Hello {first_name}", body="Body", audience={"manual": "a@example.com, b@example.com"})
    bulk.start(e)
    assert bulk.process(e, "https://crm.example")["sent"] == 2
    with pytest.raises(ValueError, match="No one"):
        bulk.start(Broadcast.objects.create(channel="sms", name="Empty", body="x", audience={"manual": ""}))
    with pytest.raises(ValueError, match="subject"):
        bulk.start(Broadcast.objects.create(channel="email", name="NoSub", body="x", audience={"manual": "z@example.com"}))


def test_live_providers_are_called_with_the_right_requests(audience, monkeypatch):
    save_credentials("sms", {"username": "sandbox", "api_key": "k-at", "sender_id": "DAYSAF"})
    save_credentials("brevo", {"api_key": "k-brevo", "sender_email": "hello@pocketofparadise.co.ke", "sender_name": "Pocket of Paradise"})
    assert bulk.sms_live() and bulk.email_live()
    calls = []

    def fake_post(url, headers, data, timeout=25):
        calls.append((url, headers, data))
        if "africastalking" in url:
            body = json.dumps({"SMSMessageData": {"Recipients": [{"number": "+254712000001", "status": "Success", "statusCode": 101, "messageId": "ATX1"},
                                                                  {"number": "+254799000001", "status": "InvalidPhoneNumber", "statusCode": 403}]}})
            return 201, body
        return 201, json.dumps({"messageId": "<brevo-1>"})
    monkeypatch.setattr(bulk, "_post", fake_post)
    b = Broadcast.objects.create(channel="sms", name="Live", body="Hello", audience={"clients": {"enabled": True}, "manual": "0799000001"})
    bulk.start(b)
    c = bulk.process(b)
    url, headers, data = calls[0]
    assert url.startswith("https://api.sandbox.africastalking.com/version1/messaging") and headers["apiKey"] == "k-at" and b"from=DAYSAF" in data and b"username=sandbox" in data
    assert c["sent"] == 1 and c["failed"] == 1 and b.recipients.get(phone="+254799000001").error == "InvalidPhoneNumber" and b.live
    bulk.retry_failed(b)
    assert b.recipients.filter(status="pending").count() == 1
    calls.clear()
    e = Broadcast.objects.create(channel="email", name="Mail", subject="Hi {first_name}", body="Text", audience={"clients": {"enabled": True}})
    bulk.start(e)
    bulk.process(e, "https://crm.example")
    url, headers, data = calls[0]
    payload = json.loads(data)
    assert url == "https://api.brevo.com/v3/smtp/email" and headers["api-key"] == "k-brevo" and payload["sender"]["email"] == "hello@pocketofparadise.co.ke"
    assert payload["subject"] == "Hi Amina" and "Unsubscribe" in payload["htmlContent"] and "/ict/unsubscribe/" in payload["htmlContent"]


def test_provider_failure_marks_failed_not_crash(audience, monkeypatch):
    save_credentials("sms", {"username": "u", "api_key": "k"})
    monkeypatch.setattr(bulk, "_post", lambda *a, **k: (401, "bad key"))
    b = Broadcast.objects.create(channel="sms", name="Bad", body="x", audience={"clients": {"enabled": True}})
    bulk.start(b)
    c = bulk.process(b)
    assert c["failed"] == 1 and "401" in b.recipients.filter(status="failed").first().error


def test_scheduling_cancel_and_job(audience):
    from apps.ict.tasks import process_broadcasts

    b = Broadcast.objects.create(channel="sms", name="Later", body="x", audience={"clients": {"enabled": True}})
    bulk.start(b, timezone.now() + timedelta(hours=2))
    assert b.status == "scheduled" and process_broadcasts() == "0 processed"
    Broadcast.objects.filter(pk=b.pk).update(scheduled_at=timezone.now() - timedelta(minutes=1))
    assert process_broadcasts() == "1 processed"
    b.refresh_from_db()
    assert b.status == "sent"
    c = Broadcast.objects.create(channel="sms", name="Stop", body="x", audience={"clients": {"enabled": True}})
    bulk.start(c, timezone.now() + timedelta(days=1))
    bulk.cancel(c)
    assert c.status == "cancelled"


def test_pages_permissions_unsubscribe(client, make_user, audience):
    ict = make_user("ict@example.com", R.ICT_OFFICER)
    client.force_login(ict)
    assert client.get(reverse("ict:broadcasts")).status_code == 200
    assert client.get(reverse("ict:broadcast_new") + "?channel=email").status_code == 200
    r = client.post(reverse("ict:broadcast_preview"), {"channel": "sms", "clients_enabled": "on"})
    assert r.json()["count"] == 1 and r.json()["skipped"] >= 1
    r = client.post(reverse("ict:broadcast_new") + "?channel=sms", {"name": "T", "body": "Hi {first_name}", "clients_enabled": "on", "when": "now", "action": "send"})
    b = Broadcast.objects.get(name="T")
    assert r.status_code == 302 and b.status == "sent"                                              # sent straight away in mock mode
    assert client.get(reverse("ict:broadcast", args=[b.pk])).status_code == 200
    sales = make_user("sales@example.com", R.SALES_AGENT)
    client.force_login(sales)
    assert client.get(reverse("ict:broadcasts")).status_code == 403
    client.logout()
    tok = bulk.unsubscribe_token("email", "amina@example.com")
    assert client.get(reverse("ict:unsubscribe", args=[tok])).status_code == 200
    assert client.post(reverse("ict:unsubscribe", args=[tok])).status_code == 200
    assert OptOut.objects.filter(value="amina@example.com").exists() and Client.objects.get(email="amina@example.com").marketing_opt_out
    assert client.get(reverse("ict:unsubscribe", args=["garbage"])).status_code == 400
