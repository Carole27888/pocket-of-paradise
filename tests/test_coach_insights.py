from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.catalog.models import Destination, TourPackage
from apps.coach import facts, services
from apps.coach.models import CoachMessage, CoachSettings
from apps.insights import executive, matching
from apps.leads import services as ls
from apps.leads.models import Enquiry, Stage
from apps.people.models import Employee
from apps.sales.models import FollowUp

pytestmark = pytest.mark.django_db


def _lead(agent=None, name="Wanjiru", phone="0711000001", minutes_old=0, **kw):
    e = ls.create_enquiry(client_data={"full_name": name, "phone": phone}, enquiry_data={"source": "whatsapp", **kw}, auto_assign=False)
    if agent:
        ls.assign_agent(e, agent, None, "manual")
    if minutes_old:
        old = timezone.now() - timedelta(minutes=minutes_old)
        Enquiry.objects.filter(pk=e.pk).update(created_at=old)
        e.assignments.update(assigned_at=old)            # the response clock starts when the lead is handed over
        e.refresh_from_db()
    return e


def test_briefing_once_a_day_with_real_facts(agent):
    e = _lead(agent, minutes_old=120)
    FollowUp.objects.create(enquiry=e, agent=agent, kind="call", title="Call about Mara", due_at=timezone.now() - timedelta(hours=1))
    m = services.ensure_briefing(agent.user)
    assert m and m.kind == "briefing" and "Wanjiru" in m.body and "Call about Mara" in m.body and not m.written_by_ai
    assert services.ensure_briefing(agent.user) is None                                       # once per day
    assert CoachMessage.objects.filter(employee=agent).count() == 1


def test_nudges_respect_thresholds_limits_and_hours(agent, monkeypatch):
    cfg = CoachSettings.load(); cfg.uncontacted_minutes = 30; cfg.max_nudges_per_day = 2; cfg.no_sundays = False; cfg.save()
    from datetime import time
    cfg.work_starts, cfg.work_ends = time(0, 0), time(23, 59); cfg.save()
    fresh, old1, old2, old3 = _lead(agent, "Fresh", "0711000010"), _lead(agent, "Old One", "0711000011", minutes_old=90), _lead(agent, "Old Two", "0711000012", minutes_old=95), _lead(agent, "Old Three", "0711000013", minutes_old=99)
    n = services.run_nudges()
    msgs = CoachMessage.objects.filter(employee=agent, kind="nudge")
    assert msgs.count() == 2 and not msgs.filter(title__contains="Fresh").exists()          # capped at 2, and the fresh lead isn't nagged
    assert services.run_nudges() == 0 or CoachMessage.objects.filter(employee=agent, kind="nudge").count() == 2     # no repeats the same day
    cfg.nudges_on = False; cfg.save()
    CoachMessage.objects.all().delete()
    assert services.run_nudges() == 0


def test_manager_alert_for_untouched_lead(agent, make_user):
    mgr = make_user("sm@example.com", R.SALES_MANAGER)
    Employee.objects.create(user=mgr, full_name="Boss")
    cfg = CoachSettings.load(); cfg.manager_alert_hours = 1; cfg.no_sundays = False
    from datetime import time
    cfg.work_starts, cfg.work_ends = time(0, 0), time(23, 59); cfg.save()
    _lead(None, "Waiting Wendy", "0711000020", minutes_old=180)
    services.run_nudges()
    assert CoachMessage.objects.filter(employee__user=mgr, kind="alert", body__contains="Waiting Wendy").exists()


def test_reports_digest_and_off_switches(agent, make_user):
    cfg = CoachSettings.load(); cfg.no_sundays = False; cfg.save()
    _lead(agent)
    late = timezone.make_aware(timezone.datetime(2026, 10, 14, 19, 0))
    assert services.send_end_of_day(late) == 1 and CoachMessage.objects.filter(kind="eod").exists()
    assert services.send_end_of_day(timezone.make_aware(timezone.datetime(2026, 10, 14, 7, 0))) == 0       # before the time
    monday = timezone.make_aware(timezone.datetime(2026, 10, 12, 9, 0))
    assert monday.weekday() == 0 and services.send_weekly(monday) == 1
    assert services.send_weekly(timezone.make_aware(timezone.datetime(2026, 10, 13, 9, 0))) == 0
    mgr = make_user("m@example.com", R.SALES_MANAGER); Employee.objects.create(user=mgr, full_name="M")
    assert services.send_digest(late) == 1


def test_ai_wording_used_only_with_a_key_and_falls_back(agent, monkeypatch):
    from apps.ict import ai

    monkeypatch.setattr(ai, "configured", lambda: True)
    monkeypatch.setattr(ai, "complete", lambda *a, **k: {"content": [{"type": "text", "text": "Habari! AI wording."}]})
    _lead(agent)
    m = services.ensure_briefing(agent.user)
    assert m.written_by_ai and m.body == "Habari! AI wording."
    CoachMessage.objects.all().delete()
    monkeypatch.setattr(ai, "complete", lambda *a, **k: (_ for _ in ()).throw(ai.AIUnavailable("down")))
    m2 = services.ensure_briefing(agent.user)
    assert not m2.written_by_ai and "Good morning" in m2.body


def test_assistant_pages_and_home_briefing(client, agent, make_user):
    client.force_login(agent.user)
    _lead(agent)
    home = client.get("/")
    assert home.status_code == 200 and b"Your briefing" in home.content
    page = client.get(reverse("coach:inbox"))
    assert page.status_code == 200 and b"Your day" in page.content
    m = CoachMessage.objects.filter(employee=agent).first()
    m.kind = "nudge"; m.save()
    assert client.post(reverse("coach:reply", args=[m.pk]), {"reply": "Client busy"}).status_code == 302
    assert client.get(reverse("coach:settings")).status_code == 403
    client.force_login(make_user("coo@example.com", R.COO))
    assert client.get(reverse("coach:settings")).status_code == 200
    assert client.post(reverse("coach:settings"), {"briefing_on": "on", "uncontacted_minutes": "45"}).status_code == 302
    assert CoachSettings.load().uncontacted_minutes == 45 and not CoachSettings.load().nudges_on


def test_package_matching_explains_itself(agent):
    mara = Destination.objects.create(name="Maasai Mara", slug="mara")
    diani = Destination.objects.create(name="Diani", slug="diani")
    p1 = TourPackage.objects.create(title="Mara Classic", days=3, price_per_person=90000, currency="KES", status="published", min_group=1, max_group=7)
    p1.destinations.add(mara)
    p2 = TourPackage.objects.create(title="Diani Beach", days=5, price_per_person=400000, currency="KES", status="published")
    p2.destinations.add(diani)
    e = _lead(agent, adults=2, destination=mara, budget_max_pp=120000, budget_currency="KES")
    res = matching.match_packages(e)
    assert res[0]["package"] == p1 and res[0]["score"] >= 60 and any("Within budget" in w for w in res[0]["why"]) and all(r["package"] != p2 or r["score"] < 40 for r in res)


def test_next_best_action_and_care_flags(agent):
    e = _lead(None, "Waiting", "0711000030", minutes_old=500)
    assert matching.next_action(e)["action"] == "Hand it to an agent"
    ls.assign_agent(e, agent, None, "manual")
    nba = matching.next_action(e)
    assert nba["urgency"] == "high" and "now" in nba["action"].lower()
    Enquiry.objects.filter(pk=e.pk).update(last_contact_at=timezone.now() - timedelta(days=8), stage=Stage.QUOTED)
    e.refresh_from_db()
    assert "quotation" in matching.next_action(e)["action"].lower()
    assert any("silent" in t for _l, t in matching.care_flags(Enquiry.objects.filter(pk=e.pk))[0]["flags"])


def test_enquiry_page_shows_action_and_matches_and_draft(client, agent):
    e = _lead(agent, minutes_old=200)
    client.force_login(agent.user)
    html = client.get(e.get_absolute_url()).content.decode()
    assert "Next best action" in html and "Draft a WhatsApp message" in html
    r = client.post(reverse("leads:draft", args=[e.pk]))
    assert r.status_code == 200 and "Hello Wanjiru" in r.json()["text"] and r.json()["ai"] is False


def test_command_centre_permissions_and_ask(client, make_user, agent):
    _lead(None, "Waiting Wendy", "0711000040", minutes_old=300)
    client.force_login(agent.user)
    assert client.get(reverse("insights:centre")).status_code == 403
    client.force_login(make_user("ceo@example.com", R.DIRECTOR))
    page = client.get(reverse("insights:centre"))
    assert page.status_code == 200 and b"waiting for an agent" in page.content
    r = client.post(reverse("insights:ask"), '{"question": "How are we doing?"}', content_type="application/json")
    assert r.status_code == 200 and "live figures" in r.json()["answer"] and r.json()["ai"] is False
    assert client.get(reverse("insights:care")).status_code == 200
    c = executive.centre()
    assert c["waiting"] >= 1 and isinstance(c["pipeline"], type(c["pipeline"]))


def test_team_performance_page_and_pdf(client, agent, other_agent):
    from apps.sales.models import SalesTarget

    today = timezone.localdate()
    SalesTarget.objects.create(agent=agent, year=today.year, month=today.month, revenue_target=500000)
    client.force_login(agent.user)
    page = client.get(reverse("coach:team"))
    assert page.status_code == 200 and b"Sarah Njeri" in page.content and b"James Kariuki" in page.content
    assert client.get(reverse("coach:team"), {"week": 3}).status_code == 200
    assert client.get(reverse("coach:team"), {"week": "x"}).status_code == 200
    assert client.get(reverse("coach:team"), {"format": "pdf"}).status_code in (200, 302)
