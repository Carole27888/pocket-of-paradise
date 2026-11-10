import hashlib
import hmac
import json

import pytest
from django.urls import reverse

from apps.accounts import roles as R
from apps.ict import meta, webhooks
from apps.ict.models import MetaEvent, WebhookDelivery
from apps.leads.models import Enquiry
from apps.marketing.models import Campaign, InboxMessage
from apps.system.services import save_credentials

pytestmark = pytest.mark.django_db


def post_json(client, url, body, **h):
    return client.post(url, json.dumps(body), content_type="application/json", **h)


def test_website_form_creates_unassigned_lead_and_logs(client):
    tok = webhooks.website_token()
    camp = Campaign.objects.create(name="Mara July", status="active")
    r = post_json(client, "/webhooks/lead/", {"name": "Wanjiru Kamau", "phone": "0712345678", "email": "w@example.com", "message": "July?", "adults": "3", "utm_campaign": "Mara July",
                                               "travel_date": "2027-02-10"}, HTTP_X_WEBHOOK_SECRET=tok)
    assert r.status_code == 201 and r.json()["ok"]
    e = Enquiry.objects.get(client__full_name="Wanjiru Kamau")
    assert e.assigned_agent is None and e.adults == 3 and e.campaign == camp and e.travel_date_from.isoformat() == "2027-02-10" and e.source == "website"
    d = WebhookDelivery.objects.get()
    assert d.ok and d.enquiry == e and d.source == "website"


def test_website_form_accepts_cf7_names_form_posts_and_redirect(client):
    tok = webhooks.website_token()
    r = client.post("/webhooks/lead/", {"token": tok, "your-name": "Amina Said", "your-phone": "0722000111", "your-message": "Hi", "redirect": "https://pocketofparadise.co.ke/thanks"})
    assert r.status_code == 302 and r["Location"] == "https://pocketofparadise.co.ke/thanks"
    assert Enquiry.objects.filter(client__full_name="Amina Said").exists()


def test_website_form_rejects_bad_token_bad_data_and_honeypot(client):
    webhooks.website_token()
    assert post_json(client, "/webhooks/lead/", {"name": "x", "phone": "1"}).status_code == 401
    assert post_json(client, "/webhooks/lead/", {"name": "x", "phone": "1"}, HTTP_X_WEBHOOK_SECRET="nope").status_code == 401
    tok = webhooks.website_token()
    assert post_json(client, "/webhooks/lead/", {"phone": "0712345678"}, HTTP_X_WEBHOOK_SECRET=tok).status_code == 400
    assert client.post("/webhooks/lead/", "{bad", content_type="application/json", HTTP_X_WEBHOOK_SECRET=tok).status_code == 400
    r = post_json(client, "/webhooks/lead/", {"name": "Bot", "phone": "0700000000", "website": "spam.com"}, HTTP_X_WEBHOOK_SECRET=tok)
    assert r.status_code == 201 and not Enquiry.objects.filter(client__full_name="Bot").exists()
    assert client.get("/webhooks/lead/").status_code == 405
    assert WebhookDelivery.objects.filter(ok=False).count() >= 3


def test_cors_preflight_respects_allowed_origins(client):
    webhooks.website_token()
    save_credentials("webhooks", {"allowed_origins": "https://pocketofparadise.co.ke"})
    ok = client.options("/webhooks/lead/", HTTP_ORIGIN="https://pocketofparadise.co.ke")
    assert ok.status_code == 204 and ok["Access-Control-Allow-Origin"] == "https://pocketofparadise.co.ke"
    assert "Access-Control-Allow-Origin" not in client.options("/webhooks/lead/", HTTP_ORIGIN="https://evil.example")


def test_legacy_marketing_url_and_env_secret_still_work(client, settings):
    settings.WEBHOOK_SECRET = "s3cret"
    r = post_json(client, "/marketing/webhooks/enquiry/", {"name": "Legacy", "phone": "0711000999"}, HTTP_X_WEBHOOK_SECRET="s3cret")
    assert r.status_code == 201


def sign(body, secret="appsecret"):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_meta_verify_and_signature(client):
    save_credentials("meta", {"verify_token": "vt", "app_secret": "appsecret", "access_token": "pt"})
    assert client.get("/webhooks/meta/", {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"}).content == b"42"
    assert client.get("/webhooks/meta/", {"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "42"}).status_code == 403
    body = json.dumps({"object": "page", "entry": []}).encode()
    assert client.post("/webhooks/meta/", body, content_type="application/json").status_code == 403
    assert client.post("/webhooks/meta/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body)).status_code == 200


def test_meta_leadgen_creates_lead_once_and_credits_campaign(client, monkeypatch):
    save_credentials("meta", {"app_secret": "appsecret", "access_token": "pt"})
    camp = Campaign.objects.create(name="Insta Mara", status="active", meta_ids="form-77, ad-9")
    monkeypatch.setattr(meta, "fetch_lead", lambda lid: {"platform": "ig", "field_data": [{"name": "full_name", "values": ["Zuri Njoroge"]}, {"name": "phone_number", "values": ["+254733555000"]},
                                                                                       {"name": "when_do_you_plan_to_travel", "values": ["December"]}, {"name": "number_of_travellers", "values": ["4"]}]})
    payload = {"object": "page", "entry": [{"id": "1", "changes": [{"field": "leadgen", "value": {"leadgen_id": "L1", "form_id": "form-77", "ad_id": "ad-9"}}]}]}
    body = json.dumps(payload).encode()
    assert client.post("/webhooks/meta/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body)).status_code == 200
    e = Enquiry.objects.get(client__full_name="Zuri Njoroge")
    assert e.source == "instagram" and e.campaign == camp and e.adults == 4 and "December" in e.notes and e.assigned_agent is None
    client.post("/webhooks/meta/", body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sign(body))      # Meta re-sends: ignored
    assert Enquiry.objects.filter(client__full_name="Zuri Njoroge").count() == 1 and MetaEvent.objects.count() == 1


def test_meta_failure_is_kept_and_retried(monkeypatch):
    calls = {"n": 0}

    def flaky(lid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise meta.MetaError("Meta said 500")
        return {"field_data": [{"name": "full_name", "values": ["Late Lead"]}, {"name": "phone_number", "values": ["0744000222"]}]}
    monkeypatch.setattr(meta, "fetch_lead", flaky)
    ev = meta.receive({"object": "page", "entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "L9"}}]}]})[0]
    assert ev.status == "failed" and not Enquiry.objects.filter(client__full_name="Late Lead").exists()
    assert meta.retry_failed() == 1 and Enquiry.objects.filter(client__full_name="Late Lead").exists()


def test_click_to_whatsapp_and_dm_and_followup_message():
    meta.simulate("whatsapp")
    e = Enquiry.objects.get(client__full_name="Ad Test")
    assert e.source in ("facebook", "instagram") and "Click-to-WhatsApp" in e.notes
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"contacts": [{"wa_id": "254700123457", "profile": {"name": "Ad Test"}}],
                                                                                                             "messages": [{"from": "254700123457", "id": "wamid.2", "type": "text", "text": {"body": "Any dates in June?"}}]}}]}]}
    meta.receive(payload)
    assert Enquiry.objects.filter(client__full_name="Ad Test").count() == 1                   # added to the open lead, not a new one
    assert "June" in "".join(e.activities.values_list("details", flat=True))
    from apps.marketing import services as ms

    ms.ensure_channels()
    meta.receive({"object": "instagram", "entry": [{"id": "1", "messaging": [{"sender": {"id": "123456"}, "message": {"mid": "m1", "text": "Price for Diani?"}}]}]})
    assert InboxMessage.objects.filter(message="Price for Diani?", channel__key="instagram").exists()


def test_webhooks_page_permissions_and_actions(client, make_user):
    ict = make_user("ict@example.com", R.ICT_OFFICER)
    client.force_login(ict)
    page = client.get(reverse("ict:webhooks"))
    assert page.status_code == 200 and b"/webhooks/lead/" in page.content
    assert client.post(reverse("ict:webhooks"), {"action": "test_website"}).status_code == 302
    assert Enquiry.objects.filter(client__full_name="Website Test Lead").exists()
    assert client.post(reverse("ict:webhooks"), {"action": "test_leadgen"}).status_code == 302 and Enquiry.objects.filter(client__full_name="Meta Test Lead").exists()
    old = webhooks.website_token()
    client.post(reverse("ict:webhooks"), {"action": "rotate"})
    assert webhooks.website_token() != old
    client.post(reverse("ict:webhooks"), {"action": "meta_keys", "verify_token": "abc", "app_secret": "sec", "access_token": "tok"})
    assert meta.status() == {"verify_token": True, "app_secret": True, "page_token": True}
    client.force_login(make_user("s@example.com", R.SALES_AGENT))
    assert client.get(reverse("ict:webhooks")).status_code == 403
