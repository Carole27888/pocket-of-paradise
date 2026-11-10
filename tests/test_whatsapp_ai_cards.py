"""WhatsApp (window/templates, alerts, receipts, retries), the director's business assistant, travel reminders, agent QR cards and the ICT screens."""
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.ict import ai, whatsapp
from apps.ict.models import AICall, ExecChat, MessagingSettings, WhatsAppTemplate, WhatsAppWindow
from apps.notifications.models import Notification, OutboundMessage
from apps.people.models import Employee
from apps.system.services import save_credentials

pytestmark = pytest.mark.django_db


@pytest.fixture
def graph(monkeypatch):
    """A fake Graph API: records each call and answers like Meta does."""
    calls = []

    def fake(method, path, payload=None, params=None):
        calls.append({"method": method, "path": path, "payload": payload, "params": params})
        if path.endswith("/messages"):
            return 200, {"messages": [{"id": f"wamid.{len(calls)}"}]}
        if path.endswith("/message_templates") and method == "POST":
            return 200, {"id": "tpl1", "status": "PENDING"}
        if path.endswith("/message_templates") and method == "GET":
            return 200, {"data": [{"id": "tpl1", "name": "staff_alert", "language": "en", "status": "APPROVED", "category": "UTILITY", "components": []}]}
        if path.endswith("/subscribed_apps"):
            return 200, {"data": [{"id": "app"}]} if method == "GET" else {"success": True}
        return 200, {"display_phone_number": "+254 700 000000", "verified_name": "Pocket of Paradise", "quality_rating": "GREEN", "name": "Days WABA"}

    monkeypatch.setattr(whatsapp, "_graph", fake)
    save_credentials("whatsapp", {"access_token": "EAAG-token", "phone_number_id": "1234", "waba_id": "999", "verify_token": "vt"})
    return calls


def _approve_defaults():
    whatsapp.ensure_default_templates()
    WhatsAppTemplate.objects.update(status=WhatsAppTemplate.APPROVED)


# ------------------------------------------------------------------ sending rules
def test_free_text_inside_the_window_template_outside(graph):
    from apps.integrations.messaging import send_message

    m = send_message("whatsapp", "0712000111", "Hello", purpose="notification")
    assert m.status == "failed" and "24 hours" in m.error and m.next_retry_at          # no template yet: refused, retry booked
    _approve_defaults()
    m = send_message("whatsapp", "0712000111", "Hello", purpose="lead_assigned", fields={"name": "Sarah", "title": "ENQ-1", "body": "Jane", "link": "https://x/l/1"})
    assert m.status == "sent" and m.template.startswith("new_lead_assigned") and m.provider_id
    sent = graph[-1]["payload"]
    assert sent["type"] == "template" and [p["text"] for p in sent["template"]["components"][0]["parameters"]] == ["Sarah", "ENQ-1", "Jane", "https://x/l/1"]
    whatsapp.mark_inbound("+254712000111")
    m = send_message("whatsapp", "0712000111", "Hi again")
    assert m.status == "sent" and not m.template and graph[-1]["payload"]["type"] == "text"


def test_template_params_are_cleaned():
    assert whatsapp.clean_param("a\nb\tc") == "a · b · c" and whatsapp.clean_param("") == "-"


def test_receipts_update_the_log_and_retries_resend(graph):
    from apps.integrations.messaging import retry_due, send_message

    whatsapp.mark_inbound("0712000222")
    m = send_message("whatsapp", "0712000222", "Hello")
    whatsapp.apply_statuses([{"id": m.provider_id, "status": "delivered", "timestamp": "1700000000"}])
    m.refresh_from_db()
    assert m.status == "delivered" and m.delivered_at
    whatsapp.apply_statuses([{"id": m.provider_id, "status": "read"}])
    m.refresh_from_db()
    assert m.status == "read" and m.read_at
    whatsapp.apply_statuses([{"id": m.provider_id, "status": "failed", "errors": [{"code": 131047, "title": "Re-engagement message"}]}])
    m.refresh_from_db()
    assert m.status == "failed" and m.payload["force_template"]
    _approve_defaults()
    m.next_retry_at = timezone.now() - timedelta(seconds=1)
    m.save()
    assert retry_due() == 1
    m.refresh_from_db()
    assert m.status == "sent" and m.template and m.attempts == 2


def test_mock_mode_without_keys_records_but_sends_nothing():
    from apps.integrations.messaging import send_message

    m = send_message("whatsapp", "0712000333", "Hello")
    assert m.status == "sent" and m.provider == "mock"


# ------------------------------------------------------------------ alerts reach people on their channel
def test_lead_assignment_alerts_the_agent_on_whatsapp(agent, graph, django_capture_on_commit_callbacks, mailoutbox):
    from apps.leads import services as ls

    _approve_defaults()
    agent.user.whatsapp_number = "0722000444"
    agent.user.notify_via = "both"
    agent.user.save()
    with django_capture_on_commit_callbacks(execute=True):
        e = ls.create_enquiry(client_data={"full_name": "Jane Doe", "phone": "0711000999"}, enquiry_data={"source": "call"}, auto_assign=False)
        ls.assign_agent(e, agent, None, "manual")
    n = Notification.objects.get(user=agent.user)
    assert n.title.startswith("New enquiry")
    wa = OutboundMessage.objects.get(channel="whatsapp", user=agent.user)
    assert wa.status == "sent" and wa.to == "+254722000444" and wa.template.startswith("new_lead_assigned") and wa.purpose == "lead_assigned"
    assert OutboundMessage.objects.filter(channel="email", user=agent.user).exists() and mailoutbox
    assert Notification.objects.filter(user=agent.user).count() == 1             # no second "New client" alert for the same lead


def test_app_only_and_switched_off(agent, django_capture_on_commit_callbacks):
    from apps.notifications.services import notify

    agent.user.whatsapp_number, agent.user.notify_via = "0722000555", "app"
    agent.user.save()
    with django_capture_on_commit_callbacks(execute=True):
        notify(agent.user, "Hello")
    assert not OutboundMessage.objects.exists()
    agent.user.notify_via = "whatsapp"
    agent.user.save()
    cfg = MessagingSettings.get()
    cfg.notifications_enabled = False
    cfg.save()
    with django_capture_on_commit_callbacks(execute=True):
        notify(agent.user, "Hello again")
    assert not OutboundMessage.objects.exists()


def test_live_popup_endpoint(client, agent):
    from apps.notifications.services import notify

    client.force_login(agent.user)
    base = client.get("/notifications/live/").json()
    notify(agent.user, "New lead", "Jane", "/leads/", channels=False)
    d = client.get(f"/notifications/live/?after={base['newest']}").json()
    assert d["unread"] == 1 and d["items"][0]["title"] == "New lead"


def test_profile_sets_whatsapp_number_and_channel(client, agent):
    client.force_login(agent.user)
    r = client.post("/accounts/profile/", {"action": "alerts", "al-whatsapp_number": "0733 111 222", "al-notify_via": "both"})
    assert r.status_code == 302
    agent.user.refresh_from_db()
    assert agent.user.whatsapp_number == "+254733111222" and agent.user.wants_email and agent.user.wants_whatsapp
    assert client.post("/accounts/profile/", {"action": "alerts", "al-whatsapp_number": "abc", "al-notify_via": "both"}).status_code == 200


# ------------------------------------------------------------------ webhook
def _wa_body(phone, text, mid="wamid.in1"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "contacts": [{"wa_id": phone, "profile": {"name": "Someone"}}], "messages": [{"from": phone, "id": mid, "type": "text", "text": {"body": text}}]}}]}]}


def test_webhook_signature_and_statuses(client, graph):
    save_credentials("whatsapp", {"app_secret": "s3cret"})
    whatsapp.mark_inbound("0712000666")
    from apps.integrations.messaging import send_message

    m = send_message("whatsapp", "0712000666", "Hi")
    body = json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"statuses": [{"id": m.provider_id, "status": "delivered"}]}}]}]}).encode()
    assert client.post("/ict/webhooks/whatsapp/", body, content_type="application/json").status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert client.post("/ict/webhooks/whatsapp/", body, content_type="application/json", headers={"X-Hub-Signature-256": sig}).status_code == 200
    m.refresh_from_db()
    assert m.status == "delivered"


# ------------------------------------------------------------------ the director's business assistant
@pytest.fixture
def director(make_user):
    u = make_user("ceo@example.com", R.DIRECTOR, full_name="Grace Director")
    u.whatsapp_number = "+254700111222"
    u.save()
    return u


def test_director_gets_business_answers_stranger_gets_the_bot(client, director, agent, graph, monkeypatch):
    from apps.ict.models import BotConversation, BotSettings

    BotSettings.objects.update_or_create(pk=1, defaults={"enabled": True})
    seen = []

    def fake(system, messages, **kw):
        seen.append(kw.get("feature"))
        if kw.get("feature") == "exec" and not any(isinstance(m["content"], list) for m in messages):
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "business_overview", "input": {"period": "week"}}], "provider": "claude"}
        if kw.get("feature") == "exec":
            result = json.loads(messages[-1]["content"][0]["content"])
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": f"New leads this week: {result['new_leads']}"}], "provider": "claude"}
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Karibu! Which month?"}]}

    monkeypatch.setattr(ai, "complete", fake)
    assert client.post("/ict/webhooks/whatsapp/", json.dumps(_wa_body("254700111222", "How many leads this week?")), content_type="application/json").status_code == 200
    chat = ExecChat.objects.get()
    assert chat.user == director and chat.answer.startswith("New leads this week:")
    reply = OutboundMessage.objects.filter(to="+254700111222", purpose="exec_assistant").get()
    assert reply.status == "sent" and not reply.template                       # they just wrote, so free text is allowed
    assert not BotConversation.objects.filter(phone="+254700111222").exists()
    client.post("/ict/webhooks/whatsapp/", json.dumps(_wa_body("254799000000", "How many leads this week?", "wamid.in2")), content_type="application/json")
    assert ExecChat.objects.count() == 1 and BotConversation.objects.filter(phone="+254799000000").exists()
    assert seen.count("bot") == 1


def test_exec_tools_return_live_numbers(director, agent):
    from apps.ict import exec_assistant
    from apps.leads import services as ls

    ls.create_enquiry(client_data={"full_name": "A B", "phone": "0711000001"}, enquiry_data={"source": "call"}, auto_assign=False)
    o = exec_assistant.run_tool(director, "business_overview", {"period": "month"})
    assert o["new_leads"] == 1 and o["waiting_unassigned"] == 1
    assert "agents" in exec_assistant.run_tool(director, "agent_performance", {"period": "week"})
    assert exec_assistant.run_tool(director, "upcoming_departures", {"days": 7})["trips"] == "No trips in that window."
    for name in ("lead_sources", "pipeline_by_stage", "outstanding_balances", "top_packages"):
        exec_assistant.run_tool(director, name, {})


def test_exec_only_for_allowed_roles(make_user, director):
    from apps.ict import exec_assistant

    sales = make_user("sm@example.com", R.SALES_MANAGER)
    sales.whatsapp_number = "+254700333444"
    sales.save()
    assert exec_assistant.exec_user_for("0700111222") == director
    assert exec_assistant.exec_user_for("0700333444") is None
    director.is_active = False
    director.save()
    assert exec_assistant.exec_user_for("0700111222") is None


def test_ai_usage_is_logged_with_cost(monkeypatch):
    save_credentials("ai", {"api_key": "sk-ant-x"})
    monkeypatch.setattr(ai, "_http", lambda url, h, p: {"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 1000, "output_tokens": 500}})
    ai.complete("s", [{"role": "user", "content": "x"}], feature="exec")
    c = AICall.objects.get()
    assert c.feature == "exec" and c.provider == "claude" and c.cost_usd == D("0.0105")


# ------------------------------------------------------------------ travel reminders
@pytest.fixture
def trip(agent):
    from apps.leads import services as ls
    from apps.sales.models import Booking, BookingStatus

    e = ls.create_enquiry(client_data={"full_name": "Wanjiru Kamau", "phone": "0712345678", "email": "w@example.com"}, enquiry_data={"source": "call"}, auto_assign=False)
    ls.assign_agent(e, agent, None, "manual")
    return Booking.objects.create(enquiry=e, client=e.client, agent=agent, title="Mara 3 days", currency="USD", total_amount=D("2000"), status=BookingStatus.DEPOSIT,
                                  travel_start=timezone.localdate() + timedelta(days=5))


def test_travel_reminders_go_once_to_agent_and_client(trip, agent, graph, mailoutbox):
    from apps.sales import reminders
    from apps.sales.models import TravelReminder

    _approve_defaults()
    cfg = MessagingSettings.get()
    cfg.reminder_hour, cfg.travel_reminder_days = 0, "5,1"
    cfg.save()
    assert reminders.run() == 2
    assert reminders.run() == 0                                                 # never twice
    trip.refresh_from_db()
    assert trip.portal_token                                                    # the client's link was created
    agent_alert = Notification.objects.get(user=agent.user, dedupe_key=f"travel:{trip.pk}:5")
    assert reverse("sales:booking_travel_day", args=[trip.pk]) in agent_alert.url
    wa = OutboundMessage.objects.get(channel="whatsapp", purpose="travel_client")
    assert wa.status == "sent" and wa.template.startswith("travel_reminder_client") and f"/my-trip/{trip.portal_token}/" in json.dumps(wa.payload)
    assert set(TravelReminder.objects.values_list("audience", flat=True)) == {"agent", "client"}
    assert any("starts" in m.subject for m in mailoutbox)


def test_late_booking_gets_only_the_closest_reminder(trip):
    from apps.sales import reminders

    cfg = MessagingSettings.get()
    cfg.travel_reminder_days = "5,1"
    cfg.save()
    trip.travel_start = timezone.localdate() + timedelta(days=1)
    trip.save()
    assert reminders.due() == [(trip, 1)]
    trip.status = "cancelled"
    trip.save()
    assert reminders.due() == []


def test_travel_day_page_and_flights(client, trip, agent):
    client.force_login(agent.user)
    url = reverse("sales:booking_travel_day", args=[trip.pk])
    r = client.get(url)
    assert r.status_code == 200 and b"Wanjiru Kamau" in r.content and b"balance" in r.content.lower()
    client.post(reverse("sales:booking_flight_add", args=[trip.pk]), {"leg": "arrival", "airline": "Kenya Airways", "flight_number": "KQ101", "from_airport": "LHR", "to_airport": "NBO"})
    assert trip.flights.count() == 1 and b"KQ101" in client.get(url).content
    from apps.sales import services

    services.enable_portal(trip)
    page = client.get(reverse("public_trip_portal", args=[trip.portal_token]))
    assert b"KQ101" in page.content and b"days to go" in page.content


# ------------------------------------------------------------------ agent QR cards
def test_card_scan_save_and_lead_goes_to_the_agent(client, agent, django_capture_on_commit_callbacks):
    from apps.sales.cards import card_for
    from apps.sales.models import CardEvent

    card = card_for(agent)
    assert client.get(card.public_path).status_code == 200
    client.get(card.public_path)                                               # a refresh is not a second scan
    v = client.get(reverse("public_card_vcard", args=[card.token]))
    assert v.status_code == 200 and b"BEGIN:VCARD" in v.content and b"Sarah Njeri" in v.content
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post(reverse("public_card_plan", args=[card.token]), {"name": "Tom Otieno", "phone": "0799 123 456", "email": "tom@example.com", "destination": "Mara and Diani",
                                                                          "adults": 2, "children": 1, "budget": "USD 1500 pp", "details": "Honeymoon"})
    assert r.status_code == 302
    ev = CardEvent.objects.get(kind="lead")
    e = ev.enquiry
    assert e.assigned_agent == agent and e.source == "agent_card" and e.brought_by == agent and "Honeymoon" in e.notes
    assert Notification.objects.filter(user=agent.user, title__startswith="New enquiry").exists()
    assert card.stats() == {"scans": 1, "saved": 1, "leads": 1, "won": 0, "conversion": 0}
    assert client.get(reverse("public_card_qr", args=[card.token])).content.startswith(b"\x89PNG")
    assert client.get(reverse("public_card_image", args=[card.token])).content.startswith(b"\x89PNG")
    back = client.get(reverse("public_card_image", args=[card.token]) + "?side=back&download=1")
    assert back.content.startswith(b"\x89PNG") and "-back.png" in back["Content-Disposition"]
    card.is_active = False
    card.save()
    assert client.get(card.public_path).status_code == 404


def test_card_pages_and_access(client, agent, other_agent, director):
    from apps.sales.cards import card_for

    client.force_login(agent.user)
    assert client.get(reverse("sales:my_card")).status_code == 200
    assert client.get(reverse("sales:cards")).status_code == 403
    other = card_for(other_agent)
    assert client.get(reverse("sales:card_detail", args=[other.pk])).status_code == 403
    client.force_login(director)
    assert client.get(reverse("sales:cards")).status_code == 200
    r = client.get(reverse("sales:card_detail", args=[other.pk]))
    assert r.status_code == 200 and b"James Kariuki" in r.content


# ------------------------------------------------------------------ ICT screens
def test_ict_whatsapp_and_ai_pages(admin_client, graph, director):
    for url in ("/ict/whatsapp/", "/ict/ai/", "/ict/whatsapp/messages/", "/ict/whatsapp/templates/new/"):
        assert admin_client.get(url).status_code == 200, url
    page = admin_client.get("/ict/whatsapp/").content
    assert b"/ict/webhooks/whatsapp/" in page
    admin_client.post("/ict/whatsapp/", {"action": "defaults"})
    assert WhatsAppTemplate.objects.count() == 4
    admin_client.post("/ict/whatsapp/", {"action": "submit_all"})
    assert set(WhatsAppTemplate.objects.values_list("status", flat=True)) == {"pending"}
    admin_client.post("/ict/whatsapp/", {"action": "sync"})
    assert WhatsAppTemplate.objects.get(name="staff_alert").status == "approved"
    admin_client.post("/ict/whatsapp/", {"action": "settings", "notifications_enabled": "on", "public_base_url": "https://erp.example.com", "max_retries": 3,
                                         "travel_reminder_days": "1, 5", "reminder_hour": 9, "remind_agents": "on", "remind_clients": "on"})
    cfg = MessagingSettings.get()
    assert cfg.travel_reminder_days == "5,1" and cfg.reminder_days == [5, 1] and cfg.public_base_url == "https://erp.example.com"
    admin_client.post("/ict/whatsapp/", {"action": "send_test", "to": "0712999888"})
    assert graph[-1]["payload"]["template"]["name"] in ("staff_alert", "hello_world")
    bad = admin_client.post("/ict/whatsapp/templates/new/", {"name": "x", "language": "en", "category": "UTILITY", "purpose": "other", "body": "{{1}} hello"})
    assert bad.status_code == 200 and b"start or end" in bad.content
    admin_client.post("/ict/ai/", {"action": "claude", "api_key": "sk-ant-1", "model": "claude-haiku-5-5", "make_default": "on"})
    assert ai.has_claude() and ai.active_provider() == "claude"
    admin_client.post("/ict/ai/", {"action": "openai", "api_key": "sk-o", "model": "gpt-4o-mini"})
    admin_client.post("/ict/ai/", {"action": "prefer", "prefer": "openai", "ai_fallback": "on"})
    assert ai.active_provider() == "openai"
    admin_client.post("/ict/ai/", {"action": "assistant", "exec_assistant_enabled": "on", "exec_roles": ["Director", "COO", "Finance Officer"]})
    assert "Finance Officer" in MessagingSettings.get().exec_role_names
    assert b"Grace Director" in admin_client.get("/ict/ai/").content


def test_ict_pages_need_configure_roles(client, agent):
    client.force_login(agent.user)
    assert client.get("/ict/whatsapp/").status_code in (302, 403)
