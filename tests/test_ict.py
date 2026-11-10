import json

import pytest

from apps.accounts import roles as R
from apps.ict import ai, assistant, services
from apps.ict.models import BotConversation, BotMessage, BotSettings
from apps.leads.models import Enquiry
from apps.notifications.models import Notification, OutboundMessage

pytestmark = pytest.mark.django_db


@pytest.fixture
def bot_on(monkeypatch):
    cfg = BotSettings.get(); cfg.enabled = True; cfg.save()
    sent = {}

    def fake(system, messages, **kw):
        sent["system"], sent["messages"] = system, messages
        return {"content": [{"type": "text", "text": sent.get("answer", "Great! Which month?")}], "stop_reason": "end_turn"}
    monkeypatch.setattr(ai, "complete", fake)
    return sent


def test_bot_replies_with_guardrails_and_captures_lead(bot_on):
    bot_on["answer"] = "Lovely, Wanjiru! A consultant will send a quote.\n[[LEAD name=Wanjiru Kamau|dates=July|adults=2|interest=Mara safari]]"
    conv = services.handle_inbound("0712345678", "Hi I want a Mara safari in July for 2", name="Wanjiru")
    assert "cannot be changed" in bot_on["system"] and "CATALOGUE" in bot_on["system"]
    conv.refresh_from_db()
    assert conv.lead_captured and conv.enquiry and Enquiry.objects.filter(source="whatsapp").count() == 1
    last = conv.messages.filter(sender="bot").last()
    assert "[[" not in last.body and OutboundMessage.objects.filter(channel="whatsapp").exists()
    services.handle_inbound("0712345678", "also in July", external_id="x1")
    services.handle_inbound("0712345678", "also in July", external_id="x1")      # duplicate delivery ignored
    assert BotMessage.objects.filter(external_id="x1").count() == 1
    assert Enquiry.objects.filter(source="whatsapp").count() == 1                 # lead captured once


def test_handoff_marker_and_person_request(bot_on, make_user):
    make_user("sm@example.com", R.SALES_MANAGER)
    bot_on["answer"] = "I'll pass you on.\n[[HANDOFF reason=wants to pay]]"
    conv = services.handle_inbound("0722000111", "how do I pay?")
    conv.refresh_from_db()
    assert conv.status == "human" and conv.handoff_reason == "wants to pay"
    assert Notification.objects.filter(title__startswith="WhatsApp chat needs a person").exists()
    n = BotMessage.objects.filter(sender="bot").count()
    services.handle_inbound("0722000111", "still there?")                           # human has it: bot stays silent
    assert BotMessage.objects.filter(sender="bot").count() == n
    c2 = services.handle_inbound("0733000222", "I want to speak to a human")
    assert c2.status == "human"


def test_ai_down_falls_back_to_person(monkeypatch):
    cfg = BotSettings.get(); cfg.enabled = True; cfg.save()
    monkeypatch.setattr(ai, "_post", lambda p: (_ for _ in ()).throw(ai.AIUnavailable("no quota")))
    conv = services.handle_inbound("0744000333", "hello")
    assert conv.status == "human" and "AI unavailable" in conv.handoff_reason
    assert conv.messages.filter(sender="bot").exists()


def test_bot_off_hands_over_without_replying():
    conv = services.handle_inbound("0755000444", "hello")
    assert conv.status == "human" and not conv.messages.filter(sender="bot").exists()


def test_takeover_reply_and_resume(admin_client, admin_user, bot_on):
    conv = services.handle_inbound("0766000555", "hi")
    admin_client.post(f"/ict/chats/{conv.pk}/", {"action": "take"})
    admin_client.post(f"/ict/chats/{conv.pk}/", {"action": "send", "body": "Hello, this is Anne"})
    conv.refresh_from_db()
    assert conv.status == "human" and conv.messages.filter(sender="agent").count() == 1
    admin_client.post(f"/ict/chats/{conv.pk}/", {"action": "resume"})
    conv.refresh_from_db()
    assert conv.status == "bot"
    for u in ("/ict/chats/", f"/ict/chats/{conv.pk}/", "/ict/analysis/", "/ict/settings/"):
        assert admin_client.get(u).status_code == 200


def test_webhook_verify_and_post(client, bot_on):
    from apps.system.services import save_credentials

    save_credentials("whatsapp", {"verify_token": "secret-token"})
    assert client.get("/ict/webhooks/whatsapp/", {"hub.mode": "subscribe", "hub.verify_token": "secret-token", "hub.challenge": "abc"}).content == b"abc"
    assert client.get("/ict/webhooks/whatsapp/", {"hub.mode": "subscribe", "hub.verify_token": "bad", "hub.challenge": "abc"}).status_code == 403
    body = {"entry": [{"changes": [{"value": {"contacts": [{"wa_id": "254712000000", "profile": {"name": "Amina"}}],
                                              "messages": [{"from": "254712000000", "id": "wamid.1", "type": "text", "text": {"body": "Hello"}}]}}]}]}
    assert client.post("/ict/webhooks/whatsapp/", json.dumps(body), content_type="application/json").status_code == 200
    assert BotConversation.objects.filter(display_name="Amina").exists()


def test_assistant_tool_loop_respects_visibility(agent, other_agent, monkeypatch, client):
    from apps.leads import services as ls

    mine = ls.create_enquiry(client_data={"full_name": "Zuri Mine", "phone": "0711000001"}, enquiry_data={"source": "call"}, auto_assign=False)
    ls.assign_agent(mine, agent, None, "manual")
    theirs = ls.create_enquiry(client_data={"full_name": "Zuri Theirs", "phone": "0711000002"}, enquiry_data={"source": "call"}, auto_assign=False)
    ls.assign_agent(theirs, other_agent, None, "manual")
    found = assistant.run_tool(agent.user, "search_enquiries", {"query": "Zuri"})
    assert [f["client"] for f in found] == ["Zuri Mine"]
    calls = []

    def fake(system, messages, **kw):
        calls.append(messages)
        if len(calls) == 1:
            return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "search_clients", "input": {"query": "Zuri"}}]}
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Found Zuri Mine."}]}
    monkeypatch.setattr(ai, "complete", fake)
    assert assistant.ask(agent.user, "find zuri") == "Found Zuri Mine."
    client.force_login(agent.user)
    r = client.post("/ict/assistant/", json.dumps({"question": "hi"}), content_type="application/json")
    assert r.status_code == 200 and "answer" in r.json()


# ---------------------------------------------------------------- bot training
@pytest.mark.django_db
def test_taught_answers_used_by_bot_without_ai(monkeypatch):
    from apps.ict import ai, services
    from apps.ict.models import BotMessage, BotSettings, KnowledgeEntry

    cfg = BotSettings.get()
    cfg.enabled = True
    cfg.save()
    KnowledgeEntry.objects.create(question="What time is check-in at the lodges?", answer="Check-in is from 2pm and check-out is by 10am.", keywords="arrival, check in")
    sent = []
    monkeypatch.setattr(services, "send_message", lambda *a, **k: sent.append(a))

    def boom(*a, **k):
        raise ai.AIUnavailable("no key")

    monkeypatch.setattr(ai, "complete", boom)
    conv = services.handle_inbound("0712345001", "Hi what time is check in at the lodge?")
    bot = list(conv.messages.filter(sender=BotMessage.BOT))
    assert bot and bot[0].body.startswith("Check-in is from 2pm")             # no AI key: the taught answer still goes out
    assert KnowledgeEntry.objects.get().times_used == 1
    # a question nothing covers still falls back and hands over
    conv2 = services.handle_inbound("0712345002", "Do you arrange hot air balloons over the Mara in July?")
    assert conv2.status == "human"


@pytest.mark.django_db
def test_training_page_try_and_suggestions(client, admin_user, db):
    from apps.catalog.models import TourPackage
    from apps.ict import knowledge
    from apps.ict.models import BotConversation, BotMessage, KnowledgeEntry

    client.force_login(admin_user)
    assert client.get("/ict/training/").status_code == 200
    client.post("/ict/training/", {"question": "Do you need a visa for Kenya?", "answer": "Most visitors apply for an eTA online.", "keywords": "eta, entry", "category": "Visas"})
    e = KnowledgeEntry.objects.get()
    assert client.get("/ict/training/", {"edit": e.pk}).status_code == 200
    client.post("/ict/training/", {"id": e.pk, "question": e.question, "answer": "Apply for an eTA online before you travel.", "keywords": "eta", "category": "Visas"})
    e.refresh_from_db()
    assert e.answer.startswith("Apply for")
    j = client.get("/ict/training/try/", {"q": "do I need a visa for kenya"}).json()
    assert j["mode"] == "taught" and j["reply"].startswith("Apply for an eTA")
    assert client.get("/ict/training/try/", {"q": ""}).status_code == 400
    conv = BotConversation.objects.create(phone="0700111222")
    for _ in range(3):
        BotMessage.objects.create(conversation=conv, sender="client", body="How much is a gorilla trekking permit in Uganda?")
    assert knowledge.unanswered()[0]["count"] == 3
    TourPackage.objects.create(title="Mara 3 days", days=3, nights=2, status="published", price_per_person=900, currency="USD", inclusions="Park fees\nMeals")
    client.post("/ict/training/seed/")
    assert KnowledgeEntry.objects.filter(source="catalog").count() >= 3
    client.post(f"/ict/training/{e.pk}/action/")
    e.refresh_from_db()
    assert not e.is_active
    client.post(f"/ict/training/{e.pk}/action/", {"action": "delete"})
    assert not KnowledgeEntry.objects.filter(pk=e.pk).exists()


@pytest.mark.django_db
def test_assistant_tools_cover_bookings_itineraries_and_taught_answers(admin_user):
    from apps.ict import assistant
    from apps.ict.models import KnowledgeEntry

    KnowledgeEntry.objects.create(question="What is the cancellation policy?", answer="Free until 30 days before travel.", keywords="refund")
    got = assistant.run_tool(admin_user, "company_knowledge", {"query": "cancellation policy refund"})
    assert got and got[0]["answer"].startswith("Free until")
    assert assistant.run_tool(admin_user, "company_knowledge", {"query": "zebra stripes"}) == {"result": "Nothing taught about that yet."}
    assert assistant.run_tool(admin_user, "search_bookings", {"query": "nobody"}) == []
    assert assistant.run_tool(admin_user, "search_itineraries", {"query": "nobody"}) == []


@pytest.mark.django_db
def test_lead_log_shows_social_and_front_desk_leads(client, django_user_model):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry

    u = django_user_model.objects.create_superuser("ictroot", "pw")
    client.force_login(u)
    c = C.objects.create(full_name="Amina Social", phone="+254700111222")
    d = C.objects.create(full_name="Brian Desk", phone="+254700333444")
    Enquiry.objects.create(client=c, source="instagram", captured_via="marketing_sync")
    Enquiry.objects.create(client=d, source="walk_in", captured_via="front_desk")
    r = client.get("/ict/lead-log/")
    assert r.status_code == 200
    assert b"Amina Social" in r.content and b"Brian Desk" in r.content
    r = client.get("/ict/lead-log/?channel=social")
    assert b"Amina Social" in r.content and b"Brian Desk" not in r.content
    r = client.get("/ict/lead-log/?channel=frontdesk")
    assert b"Brian Desk" in r.content and b"Amina Social" not in r.content
    assert client.get("/ict/lead-log/?export=csv").status_code == 200


@pytest.mark.django_db
def test_coo_adds_a_lead_source_and_it_is_usable(client, make_user):
    from apps.contacts.models import Client as C
    from apps.leads.models import CustomSource, Enquiry, source_choices

    coo = make_user("coo@example.com", R.COO)
    client.force_login(coo)
    r = client.post("/enquiries/sources/", {"action": "add", "label": "Nairobi Travel Expo", "badge": "ex"})
    assert r.status_code == 302
    src = CustomSource.objects.get(code="nairobi_travel_expo")
    assert ("nairobi_travel_expo", "Nairobi Travel Expo") in source_choices()
    e = Enquiry.objects.create(client=C.objects.create(full_name="Expo Visitor", phone="+254700555666"), source=src.code)
    assert e.get_source_display() == "Nairobi Travel Expo" and e.source_badge[0] == "EX"
    assert client.get("/enquiries/").status_code == 200 and client.get("/ict/lead-log/?source=nairobi_travel_expo").status_code == 200
    # in-use sources cannot be deleted, only hidden
    client.post("/enquiries/sources/", {"action": "delete", "pk": src.pk})
    assert CustomSource.objects.filter(pk=src.pk).exists()
    client.post("/enquiries/sources/", {"action": "toggle", "pk": src.pk})
    assert ("nairobi_travel_expo", "Nairobi Travel Expo") not in source_choices()


@pytest.mark.django_db
def test_only_coo_or_admin_can_add_sources(client, make_user):
    from apps.leads.models import CustomSource

    client.force_login(make_user("agent9@example.com", R.SALES_AGENT))
    client.post("/enquiries/sources/", {"action": "add", "label": "Sneaky"})
    assert not CustomSource.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("role,allowed", [(R.SALES_MANAGER, True), (R.OPERATIONS, True), (R.COO, True), (R.ADMIN, True),
                                          (R.RESERVATIONS, False), (R.FRONTDESK_MANAGER, False), (R.SALES_AGENT, False), (R.MARKETING, False)])
def test_who_may_add_tour_packages(client, make_user, role, allowed):
    client.force_login(make_user(f"{role.lower().replace(' ', '')}@example.com", role))
    r = client.get("/catalog/packages/new/")
    assert (r.status_code == 200) is allowed, (role, r.status_code)


@pytest.mark.django_db
def test_agent_can_edit_own_call_notes_but_not_anothers(client, agent, other_agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales.models import CallLog

    e = Enquiry.objects.create(client=C.objects.create(full_name="Call Edit", phone="+254700999111"), source="call", assigned_agent=agent)
    call = CallLog.objects.create(enquiry=e, agent=agent, outcome="connected", notes="old note", duration_seconds=120)
    client.force_login(agent.user)
    assert client.get(f"/sales/calls/{call.pk}/edit/").status_code == 200
    r = client.post(f"/sales/calls/{call.pk}/edit/", {"direction": "out", "outcome": "interested", "minutes": 7,
                                                      "called_at": "2026-11-01T10:30", "notes": "new note: wants quote"})
    assert r.status_code == 302
    call.refresh_from_db()
    assert call.notes == "new note: wants quote" and call.outcome == "interested" and call.duration_seconds == 420
    client.force_login(other_agent.user)
    assert client.get(f"/sales/calls/{call.pk}/edit/").status_code == 404   # not in their scope
    client.force_login(agent.user)
    assert f"/sales/calls/{call.pk}/edit/" in client.get("/sales/calls/").content.decode()


@pytest.mark.django_db
def test_call_edit_opens_as_a_modal_and_saves_via_ajax(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales.models import CallLog

    e = Enquiry.objects.create(client=C.objects.create(full_name="Modal Edit", phone="+254700999222"), source="call", assigned_agent=agent)
    call = CallLog.objects.create(enquiry=e, agent=agent, outcome="connected", notes="before")
    client.force_login(agent.user)
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    r = client.get(f"/sales/calls/{call.pk}/edit/", **ajax)
    assert r.status_code == 200 and b"data-modal-form-body" in r.content and b"<html" not in r.content
    bad = client.post(f"/sales/calls/{call.pk}/edit/", {"direction": "out", "outcome": "connected", "called_at": "nonsense"}, **ajax)
    assert bad.status_code == 422 and b"data-modal-form-body" in bad.content
    ok = client.post(f"/sales/calls/{call.pk}/edit/", {"direction": "out", "outcome": "connected", "minutes": 3,
                                                        "called_at": "2026-11-02T09:00", "notes": "after"}, **ajax)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    call.refresh_from_db()
    assert call.notes == "after"
    assert 'data-modal-form="/sales/calls/%d/edit/"' % call.pk in client.get("/sales/calls/").content.decode()


@pytest.mark.django_db
def test_kyc_list_only_offers_clients_the_agent_can_open(client, agent, other_agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry

    mine = C.objects.create(full_name="Mine Client", phone="+254700123001")
    Enquiry.objects.create(client=mine, source="call", assigned_agent=agent)
    theirs = C.objects.create(full_name="Theirs Client", phone="+254700123002")
    Enquiry.objects.create(client=theirs, source="call", assigned_agent=other_agent)
    client.force_login(agent.user)
    page = client.get("/deals/kyc/")
    assert page.status_code == 200
    body = page.content.decode()
    assert "Mine Client" in body and "Theirs Client" not in body
    assert client.get(f"/deals/kyc/client/{mine.pk}/").status_code == 200
    assert client.get(f"/deals/kyc/client/{theirs.pk}/").status_code == 404


@pytest.mark.django_db
def test_add_kyc_is_a_popup_with_inline_errors(client, agent):
    from apps.contacts.models import Client as C
    from apps.dealdesk.models import ClientKYC
    from apps.leads.models import Enquiry

    c = C.objects.create(full_name="Kyc Popup", phone="+254700123777")
    Enquiry.objects.create(client=c, source="call", assigned_agent=agent)
    client.force_login(agent.user)
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    url = f"/deals/kyc/client/{c.pk}/"
    r = client.get(url, **ajax)
    assert r.status_code == 200 and b"data-modal-form-body" in r.content and b"<html" not in r.content
    bad = client.post(url, {"id_type": "national_id", "id_number": " "}, **ajax)
    assert bad.status_code == 422 and b"Enter the ID" in bad.content
    ok = client.post(url, {"id_type": "national_id", "id_number": "12345678"}, **ajax)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    assert ClientKYC.objects.filter(client=c).exists()
    c2 = C.objects.create(full_name="Kyc Second", phone="+254700123778")
    Enquiry.objects.create(client=c2, source="call", assigned_agent=agent)
    assert f'data-modal-form="/deals/kyc/client/{c2.pk}/"' in client.get("/deals/kyc/").content.decode()


@pytest.mark.django_db
def test_weekly_plan_nudge_marking_and_privacy(client, make_user):
    from datetime import timedelta

    from django.utils import timezone

    from apps.system import planning
    from apps.system.models import WeeklyPlanItem as P

    u = make_user("planner@example.com", R.SALES_AGENT)
    client.force_login(u)
    today = timezone.localdate()
    assert planning.nudge(u)["kind"] == "plan"            # nobody has planned: it is required
    assert b"Required" in client.get("/system/todo/").content and b"Required" in client.get("/").content
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    assert client.get("/system/plan/add/", **ajax).status_code == 200
    bad = client.post("/system/plan/add/", {"title": "", "days": [today.isoformat()]}, **ajax)
    assert bad.status_code == 422
    ok = client.post("/system/plan/add/", {"title": "Chase quotes", "start_time": "09:30", "days": [today.isoformat(), P.monday(today).isoformat()]}, **ajax)
    assert ok.json() == {"ok": True}
    assert P.objects.filter(user=u).count() == len({today, P.monday(today)})
    item = P.objects.filter(user=u, day=today).first()
    assert planning.nudge(u, timezone.localtime().replace(hour=17)) ["kind"] == "mark"   # today's work is unmarked after 4pm
    client.post(f"/system/plan/{item.pk}/mark/", {"status": "done"})
    item.refresh_from_db()
    assert item.status == "done"
    assert client.get(f"/system/plan/{item.pk}/mark/?status=partial", **ajax).status_code == 200
    client.post(f"/system/plan/{item.pk}/mark/", {"status": "partial", "note": "half way"}, **ajax)
    item.refresh_from_db()
    assert item.status == "partial" and item.note == "half way"
    future = P.objects.create(user=u, day=today + timedelta(days=1), title="Tomorrow")
    assert client.post(f"/system/plan/{future.pk}/mark/", {"status": "done"}).status_code == 403
    other = make_user("other@example.com", R.SALES_AGENT)
    client.force_login(other)
    assert client.post(f"/system/plan/{item.pk}/mark/", {"status": "done"}).status_code == 404   # not theirs
    assert client.get("/system/team-plans/").status_code == 403
    client.force_login(make_user("coo2@example.com", R.COO))
    assert client.get("/system/team-plans/").status_code == 200


@pytest.mark.django_db
def test_reports_hub_builds_schedules_pauses_and_removes(client, agent):
    from apps.reports.models import ScheduledReport

    client.force_login(agent.user)
    page = client.get("/reports/")
    assert page.status_code == 200 and b"Build a report" in page.content and b"Scheduled reports" in page.content
    key = "leads_by_source"
    r = client.post(f"/reports/{key}/schedule/", {"frequency": "weekly", "range_days": 30, "from_hub": 1})
    assert r.status_code == 302 and r["Location"] == "/reports/"
    s = ScheduledReport.objects.get(user=agent.user, report_key=key)
    assert s.active and "Last 30 days" in client.get("/reports/").content.decode()
    client.post(f"/reports/{key}/schedule/", {"action": "pause", "from_hub": 1})
    s.refresh_from_db()
    assert not s.active and b"Paused" in client.get("/reports/").content
    client.post(f"/reports/{key}/schedule/", {"action": "resume", "from_hub": 1})
    s.refresh_from_db()
    assert s.active
    client.post(f"/reports/{key}/schedule/", {"action": "remove", "from_hub": 1})
    assert not ScheduledReport.objects.filter(pk=s.pk).exists()
    assert client.get(f"/reports/{key}/?format=csv").status_code == 200


@pytest.mark.django_db
def test_my_profile_edits_details_and_flags_payroll_changes_to_finance(client, make_user):
    from apps.notifications.models import Notification
    from apps.people.models import Employee

    u = make_user("profile@example.com", R.SALES_AGENT, full_name="Pro File")
    emp = Employee.objects.create(user=u, full_name="Pro File", is_sales_agent=True, payout_method="bank", bank_name="Old Bank", bank_account="111")
    fin = make_user("fin@example.com", R.FINANCE)
    client.force_login(u)
    page = client.get("/accounts/profile/")
    assert page.status_code == 200 and b"Payroll" in page.content and b"Go to Security" in page.content
    base = {"full_name": "Pro File", "phone": "+254700000001", "email": "pro@example.com", "payout_method": "bank", "kra_pin": "", "bank_name": "Old Bank", "bank_account": "111", "mpesa_number": "", "nssf_no": "", "shif_no": ""}
    client.post("/accounts/profile/", {**base, "phone": "+254700000002"})        # contact only: nothing for Finance
    assert not Notification.objects.filter(user=fin).exists()
    client.post("/accounts/profile/", {**base, "bank_account": "222"})           # payroll: Finance is told
    emp.refresh_from_db()
    assert emp.bank_account == "222"
    assert Notification.objects.filter(user=fin, title__contains="changed payroll").exists()
    html = client.get("/").content.decode()
    assert "/accounts/profile/" in html


@pytest.mark.django_db
def test_admin_switches_two_factor_on_and_off_and_email_is_read_only(client, make_user, settings):
    from apps.accounts import twofactor
    from apps.people.models import Employee

    settings.TWO_FACTOR_ENABLED = False
    admin = make_user("boss@example.com", R.ADMIN)
    client.force_login(admin)
    assert not twofactor.feature_on()
    assert b"Turn two-factor ON" in client.get("/system/security/").content
    client.post("/system/security/", {"action": "on"})
    assert twofactor.feature_on() and twofactor.requires_2fa(admin)
    assert b"Turn two-factor OFF" in client.get("/system/security/").content
    client.post("/system/security/", {"action": "off"})
    assert not twofactor.feature_on()
    settings.TWO_FACTOR_ENABLED = True            # forced by the server: the button cannot override it
    client.post("/system/security/", {"action": "off"})
    assert twofactor.feature_on()
    settings.TWO_FACTOR_ENABLED = False
    agent_user = make_user("notadmin@example.com", R.SALES_AGENT)
    client.force_login(agent_user)
    assert client.get("/system/security/").status_code == 403
    # the profile e-mail cannot be changed by posting a new one
    emp = Employee.objects.create(user=agent_user, full_name="No Mail Change", email="fixed@example.com")
    client.post("/accounts/profile/", {"full_name": "No Mail Change", "phone": "", "email": "hacked@example.com", "payout_method": "bank",
                                       "kra_pin": "", "bank_name": "", "bank_account": "", "mpesa_number": "", "nssf_no": "", "shif_no": ""})
    emp.refresh_from_db()
    assert emp.email == "fixed@example.com"
    assert b"disabled" in client.get("/accounts/profile/").content


@pytest.mark.django_db
def test_notice_board_modal_post_edit_and_visibility(client, make_user):
    from apps.notifications.models import Announcement

    coo = make_user("coo3@example.com", R.COO)
    agent_u = make_user("agent3@example.com", R.SALES_AGENT)
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    client.force_login(agent_u)
    assert client.get("/notifications/board/new/", **ajax).status_code == 403          # only management posts
    client.force_login(coo)
    assert client.get("/notifications/board/new/", **ajax).status_code == 200
    assert client.post("/notifications/board/new/", {"title": "", "body": ""}, **ajax).status_code == 422
    ok = client.post("/notifications/board/new/", {"title": "Hello team", "body": "Welcome", "level": "important", "pinned": "on", "audience": [R.SALES_AGENT]}, **ajax)
    assert ok.json() == {"ok": True}
    n = Announcement.objects.get(title="Hello team")
    assert n.level == "important" and n.pinned and n.roles == [R.SALES_AGENT]
    client.post(f"/notifications/board/{n.pk}/edit/", {"title": "Hello everyone", "body": "Welcome all"}, **ajax)
    n.refresh_from_db()
    assert n.title == "Hello everyone" and n.roles == [] and not n.pinned
    page = client.get("/notifications/board/")
    assert page.status_code == 200 and b"Active notices" in page.content and b"Hello everyone" in page.content and b"Post a notice" in page.content
    client.force_login(agent_u)
    page = client.get("/notifications/board/")
    assert b"Hello everyone" in page.content and b"Post a notice" not in page.content


@pytest.mark.django_db
def test_sales_automation_creates_each_followup_once_and_respects_switches(agent, make_user):
    from datetime import timedelta
    from decimal import Decimal

    from django.utils import timezone

    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales import automation, services
    from apps.sales.models import Booking, BookingStatus, FollowUp, Quotation, QuotationStatus, SalesSettings

    cfg = SalesSettings.get()
    now = timezone.now()
    c = C.objects.create(full_name="Auto Client", phone="+254700555001")
    e = Enquiry.objects.create(client=c, source="call", stage="quoted", assigned_agent=agent, last_contact_at=now - timedelta(days=9))
    Enquiry.objects.filter(pk=e.pk).update(stage_changed_at=now - timedelta(days=9))
    q = Quotation.objects.create(enquiry=e, agent=agent, title="Trip", status=QuotationStatus.SENT, sent_at=now - timedelta(days=3), currency="KES", fx_rate=1)
    first = automation.run_all(cfg)
    assert first["quote_chase"] == 1
    assert FollowUp.objects.filter(auto_key=f"quote-chase:{q.pk}").count() == 1
    assert sum(automation.run_all(cfg).values()) == 0                         # idempotent: nothing is created twice
    # a booking that needs a deposit, a balance, a pre-trip check and feedback
    e2 = Enquiry.objects.create(client=C.objects.create(full_name="Trip Client", phone="+254700555002"), source="call", stage="deposit", assigned_agent=agent)
    today = timezone.localdate()
    b = Booking.objects.create(enquiry=e2, client=e2.client, agent=agent, title="T", total_amount=Decimal("1000"), currency="KES", fx_rate=1,
                               status=BookingStatus.PENDING, booked_at=now - timedelta(days=5), travel_start=today + timedelta(days=5), travel_end=today + timedelta(days=8))
    made = automation.run_all(cfg)
    assert made["deposit"] == 1
    Booking.objects.filter(pk=b.pk).update(status=BookingStatus.DEPOSIT)
    made = automation.run_all(cfg)
    assert made["balance"] == 1 and made["pretrip"] == 1
    b.refresh_from_db()
    assert b.portal_token                                                      # the trip page is ready for the client
    Booking.objects.filter(pk=b.pk).update(travel_start=today - timedelta(days=5), travel_end=today - timedelta(days=2))
    assert automation.run_all(cfg)["feedback"] == 1
    # switches: master off creates nothing; a rule at 0 is skipped
    FollowUp.objects.filter(auto_key__startswith="feedback").delete()
    cfg.auto_feedback_after_days = 0
    assert automation.run_all(cfg)["feedback"] == 0
    cfg.automation_on = False
    assert automation.run_all(cfg) == {}
    cfg.automation_on = True
    # the next step after a call is scheduled when the agent does not do it
    e3 = Enquiry.objects.create(client=C.objects.create(full_name="Call Client", phone="+254700555003"), source="call", stage="enquiry", assigned_agent=agent)
    services.log_call(e3, agent.user, outcome="interested")
    assert FollowUp.objects.filter(enquiry=e3, kind="send_quote", auto_key__startswith="call-next").count() == 1


@pytest.mark.django_db
def test_automation_page_is_for_managers_and_can_run_now(client, agent, make_user):
    client.force_login(agent.user)
    assert client.get("/sales/automation/").status_code == 403
    client.force_login(make_user("sm9@example.com", R.SALES_MANAGER))
    page = client.get("/sales/automation/")
    assert page.status_code == 200 and b"Chase a quotation" in page.content and b"Run now" in page.content
    r = client.post("/sales/automation/", {"action": "run"})
    assert r.status_code == 302
    from apps.sales.models import SalesSettings

    client.post("/sales/automation/", {"automation_on": "on", "auto_quote_chase_days": 4, "auto_reconnect_days": 0, "auto_deposit_chase_days": 3,
                                       "auto_balance_chase_days": 30, "auto_pretrip_days": 7, "auto_feedback_after_days": 1})
    cfg = SalesSettings.get()
    assert cfg.auto_quote_chase_days == 4 and cfg.auto_reconnect_days == 0 and not cfg.auto_callback_after_call


@pytest.mark.django_db
def test_plan_reminders_notify_once_and_respect_the_switch(make_user, settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.notifications.models import Notification
    from apps.people.models import Employee
    from apps.system import tasks as system_tasks
    from apps.system.models import WeeklyPlanItem as P

    u = make_user("remind@example.com", R.SALES_AGENT)
    Employee.objects.create(user=u, full_name="Re Mind", is_sales_agent=True)
    today = timezone.localdate()
    if today.weekday() != 0:
        pytest.skip("the Monday reminder only fires on Mondays")
    assert "1 reminded" in system_tasks.plan_week_reminder()
    assert Notification.objects.filter(user=u, title="Plan your week").count() == 1
    system_tasks.plan_week_reminder()
    assert Notification.objects.filter(user=u, title="Plan your week").count() == 1   # not twice


def test_day_story_drops_the_repeated_title_and_activities_sentence():
    from apps.sales.models import LeadItineraryDay

    d = LeadItineraryDay(title="Nairobi to Maasai Mara", description="Nairobi to Maasai Mara. Activities: afternoon game drive.", activities="Afternoon game drive")
    assert d.story == ""
    d2 = LeadItineraryDay(title="Samburu to Lake Nakuru", description="Samburu to Lake Nakuru. Settle in for a scenic drive. Activities: rhino tracking.", activities="Rhino tracking")
    assert d2.story == "Settle in for a scenic drive."
    d3 = LeadItineraryDay(title="Full day", description="A full day on safari with a picnic lunch.", activities="")
    assert d3.story == "A full day on safari with a picnic lunch."


@pytest.mark.django_db
def test_moving_a_lead_to_quoted_or_hot_needs_a_quotation(client, agent):
    import json

    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales.models import Quotation

    e = Enquiry.objects.create(client=C.objects.create(full_name="Needs Quote", phone="+254700777001"), source="call", stage="enquiry", assigned_agent=agent)
    client.force_login(agent.user)
    # board drag: refused with a prompt that points at the quotation form
    r = client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "quoted"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert r.status_code == 409 and r.json()["need_quote"] and f"enquiry={e.pk}" in r.json()["url"]
    e.refresh_from_db()
    assert e.stage == "enquiry"
    assert client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "hot"}).status_code == 409
    # lead page (HTMX): no change, and the server asks the page to open the prompt
    r = client.post(f"/enquiries/{e.pk}/stage/", {"stage": "quoted"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and json.loads(r["HX-Trigger"])["needQuote"]["reference"] == e.reference
    e.refresh_from_db()
    assert e.stage == "enquiry"
    # bulk: not moved, and the user is told why
    client.post("/enquiries/bulk/stage/", {"ids": [e.pk], "stage": "quoted"}, HTTP_HX_REQUEST="true")
    e.refresh_from_db()
    assert e.stage == "enquiry"
    # other stages are unaffected, and once a quotation exists the move works
    assert client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "itinerary"}).status_code == 409   # Itinerary sent needs one too
    assert client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "lost", "reason": "Price"}).status_code == 200   # losing a lead never does
    Enquiry.objects.filter(pk=e.pk).update(stage="enquiry")
    Quotation.objects.create(enquiry=e, agent=agent, title="T", currency="KES", fx_rate=1)
    assert client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "quoted"}).status_code == 200
    e.refresh_from_db()
    assert e.stage == "quoted"


@pytest.mark.django_db
def test_build_itinerary_page_lists_quoted_itineraries(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales.models import LeadItinerary, Quotation

    e = Enquiry.objects.create(client=C.objects.create(full_name="Quoted Trip", phone="+254700888001"), source="call", assigned_agent=agent)
    q = Quotation.objects.create(enquiry=e, agent=agent, title="T", currency="KES", fx_rate=1)
    LeadItinerary.objects.create(enquiry=e, title="Quoted safari", quotation=q)
    LeadItinerary.objects.create(enquiry=e, title="Not quoted safari")
    client.force_login(agent.user)
    page = client.get("/sales/itineraries/new/").content.decode()
    assert "Quoted itineraries" in page and q.reference in page
    quoted_block = page.split("Quoted itineraries")[1]
    assert "Quoted safari" in quoted_block and "Not quoted safari" not in quoted_block


@pytest.mark.django_db
def test_build_itinerary_does_not_offer_leads_that_are_already_quoted(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales.models import Quotation

    fresh = Enquiry.objects.create(client=C.objects.create(full_name="Fresh Lead", phone="+254700999501"), source="call", assigned_agent=agent)
    quoted = Enquiry.objects.create(client=C.objects.create(full_name="Already Quoted", phone="+254700999502"), source="call", assigned_agent=agent, stage="quoted")
    Quotation.objects.create(enquiry=quoted, agent=agent, title="T", currency="KES", fx_rate=1)
    client.force_login(agent.user)
    page = client.get("/sales/itineraries/new/").content.decode()
    assert fresh.reference in page and quoted.reference not in page
    # opened from the quoted lead's own page it is preselected, so building another option for it still works
    assert client.get(f"/sales/itineraries/new/?enquiry={quoted.pk}").status_code == 200
    r = client.post(f"/sales/itineraries/new/?enquiry={quoted.pk}", {"enquiry": quoted.pk, "start": "", "title": "Option B"})
    assert r.status_code == 302


@pytest.mark.django_db
def test_standard_terms_are_set_by_operations_and_fill_new_itineraries_and_quotes(client, agent, make_user):
    from apps.contacts.models import Client as C
    from apps.core.models import DEFAULT_TERMS, CompanySetting
    from apps.leads.models import Enquiry
    from apps.sales import services
    from apps.sales.models import LeadItinerary

    e = Enquiry.objects.create(client=C.objects.create(full_name="Terms Client", phone="+254700999601"), source="call", assigned_agent=agent)
    assert services.create_itinerary(e, agent.user).terms == DEFAULT_TERMS                    # the built-in default object
    ops = make_user("ops9@example.com", R.OPERATIONS)
    client.force_login(ops)
    assert client.get("/operations/standard-terms/").status_code == 200
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    assert client.post("/operations/standard-terms/", {"standard_terms": " "}, **ajax).status_code == 422
    assert client.post("/operations/standard-terms/", {"standard_terms": "50% deposit.\nNo refund inside 14 days."}, **ajax).json() == {"ok": True}
    assert CompanySetting.load().standard_terms.startswith("50% deposit")
    it = services.create_itinerary(e, agent.user)
    assert it.terms.startswith("50% deposit")
    q = services.create_quotation(e, agent.user)
    assert q.terms.startswith("50% deposit")
    blank = LeadItinerary.objects.create(enquiry=e, title="Old one")                          # an older itinerary with no terms of its own
    assert blank.effective_terms.startswith("50% deposit")
    from apps.sales.forms import ItineraryForm
    assert ItineraryForm(instance=blank).initial["terms"].startswith("50% deposit")           # the edit form pre-fills it
    client.post("/operations/standard-terms/", {"action": "reset"}, **ajax)
    assert CompanySetting.load().standard_terms == DEFAULT_TERMS
    client.force_login(agent.user)
    assert client.get("/operations/standard-terms/").status_code in (403, 404)                # agents do not change them


@pytest.mark.django_db
def test_travel_options_are_alternatives_priced_into_the_itinerary_and_quote(client, agent):
    from decimal import Decimal

    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales import services
    from apps.sales.models import ItineraryTravel

    e = Enquiry.objects.create(client=C.objects.create(full_name="Travel Client", phone="+254700999701"), source="call", assigned_agent=agent, adults=4, children=0)
    it = services.create_itinerary(e, agent.user)
    assert it.travel_from == Decimal("0")
    ItineraryTravel.objects.create(itinerary=it, mode="noah", name="Noah van", basis="total", price=Decimal("30000"), notes="7 seats, fuel included")
    ItineraryTravel.objects.create(itinerary=it, mode="train", name="SGR economy", basis="person", price=Decimal("1500"))
    ItineraryTravel.objects.create(itinerary=it, mode="cruiser", name="Land Cruiser", basis="total", price=Decimal("45000"))
    it = type(it).objects.get(pk=it.pk)
    assert it.travel_from == Decimal("6000.00")                                  # the cheapest (SGR: 1,500 x 4) sets the headline price
    assert it.sell_total == it.day_sell + it.lines_total + it.hotel_from + Decimal("6000.00")
    # the edit page shows the section and saves rows added in the form
    client.force_login(agent.user)
    page = client.get(f"/sales/itineraries/{it.pk}/edit/").content.decode()
    assert "Travel options" in page and "Lodge options" in page and "Add travel option" in page and 'class="rm-x"' in page
    from apps.sales.forms import ItineraryTravel as _T                           # noqa: F401
    # copies and quotations carry them
    n = services.duplicate_itinerary(it, agent.user)
    assert n.travel_options.count() == 3
    q = services.itinerary_to_quotation(it, agent.user)
    assert any("SGR economy" in i.description for i in q.items.all())
    # the client PDF context lists them with the extra cost against the cheapest
    from apps.sales import itinerary_pdf
    rows = itinerary_pdf.travel_rows(it)
    assert [r["t"].name for r in rows] == ["Noah van", "SGR economy", "Land Cruiser"]
    assert rows[1]["cheapest"] and rows[0]["extra"] == Decimal("24000.00")


def test_travel_options_include_a_coaster_van():
    from apps.sales.models import ItineraryTravel

    assert ("coaster", "Coaster van") in ItineraryTravel.MODES
    assert dict(ItineraryTravel.MODES)["noah"] == "Noah / van" and dict(ItineraryTravel.MODES)["train"] == "SGR train"


def test_file_button_styling_never_touches_the_hidden_picture_inputs():
    """A broad file-input style once stretched the hidden .dz-input boxes and made the itinerary page scroll sideways."""
    css = open("static/css/vibrant.css").read()
    lines = [l for l in css.splitlines() if l.startswith('input[type="file"]')]
    assert lines and all(":not(.dz-input)" in l for l in lines)


@pytest.mark.django_db
def test_a_quotation_must_come_from_an_itinerary(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales import services
    from apps.sales.models import Quotation

    e = Enquiry.objects.create(client=C.objects.create(full_name="Needs Itin", phone="+254700999801"), source="call", assigned_agent=agent)
    client.force_login(agent.user)
    # no itinerary: asking for a quotation sends the agent to the itinerary builder instead
    r = client.get(f"/sales/quotations/new/?enquiry={e.pk}")
    assert r.status_code == 302 and "/sales/itineraries/new/" in r["Location"] and f"enquiry={e.pk}" in r["Location"]
    assert not Quotation.objects.filter(enquiry=e).exists()
    # the stage prompt says the same thing and points at the builder
    p = client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "quoted"})
    assert p.status_code == 409 and p.json()["no_itinerary"] and "/sales/itineraries/new/" in p.json()["url"]
    # a draft quotation with no itinerary behind it cannot be sent
    q = Quotation.objects.create(enquiry=e, agent=agent, title="T", currency="KES", fx_rate=1)
    from apps.sales.models import QuotationItem
    QuotationItem.objects.create(quotation=q, position=0, description="x", quantity=1, unit_price=100)
    client.post(f"/sales/quotations/{q.pk}/send/", {"via": "email"})
    q.refresh_from_db()
    assert q.status == "draft"
    # with an itinerary, the quotation is made from it and linked to it
    it = services.create_itinerary(e, agent.user)
    from apps.sales.models import ItineraryPriceLine
    ItineraryPriceLine.objects.create(itinerary=it, description="Safari package", quantity=1, unit_price=50000)
    r = client.get(f"/sales/quotations/new/?enquiry={e.pk}")
    assert r.status_code == 302 and "/edit/" in r["Location"]
    it.refresh_from_db()
    assert it.quotation_id and it.quotation.enquiry_id == e.pk
    p = client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "quoted"})
    assert p.status_code == 200


@pytest.mark.django_db
def test_itinerary_builder_has_summary_bar_sections_and_helpers(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales import services

    e = Enquiry.objects.create(client=C.objects.create(full_name="Builder UX", phone="+254700999901"), source="call", assigned_agent=agent)
    it = services.create_itinerary(e, agent.user)
    client.force_login(agent.user)
    page = client.get(f"/sales/itineraries/{it.pk}/edit/").content.decode()
    for needle in ('id="ib-bar"', 'id="ib-ready"', 'href="#ib-lodges"', 'id="ib-days"', 'id="ib-travel"', "data-days-toggle", "itinerary-builder.js", 'data-floor="'):
        assert needle in page, needle
    assert f"/sales/itineraries/{it.pk}/print/" in page                         # the client preview link
    js = open("static/js/itinerary-builder.js").read()
    assert "beforeunload" in js and "itinDays.addRow" in js                     # unsaved-changes guard and Duplicate day
    assert "window.itinDays" in open("static/js/itinerary-dnd.js").read()


@pytest.mark.django_db
def test_client_view_offers_only_the_designed_pdf_download(client, agent):
    from apps.contacts.models import Client as C
    from apps.leads.models import Enquiry
    from apps.sales import services

    e = Enquiry.objects.create(client=C.objects.create(full_name="Pdf Only", phone="+254700999902"), source="call", assigned_agent=agent)
    it = services.create_itinerary(e, agent.user)
    client.force_login(agent.user)
    page = client.get(f"/sales/itineraries/{it.pk}/print/").content.decode()
    assert "Download designed PDF" in page and f"/sales/itineraries/{it.pk}/pdf/?download=1" in page
    assert "Print / Save as PDF" not in page
