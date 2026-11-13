"""Competitor Watch (website changes, Google profile, web search, access) and the Front Desk weekend roster."""
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.insights import competitors as cw
from apps.insights.models import Competitor, MarketSearch, Snapshot
from apps.notifications.models import Notification
from apps.system.services import save_credentials

pytestmark = pytest.mark.django_db

PAGE_1 = "<html><head><title>Acme Safaris</title><script>var x=1</script></head><body><h1>Our safaris</h1><p>3-day Mara from USD 450 pp</p><p>Diani beach week</p></body></html>"
PAGE_2 = "<html><head><title>Acme Safaris</title></head><body><h1>Our safaris</h1><p>3-day Mara from USD 399 pp</p><p>Diani beach week</p><p>NEW: Amboseli weekend KES 25,000</p></body></html>"


@pytest.fixture
def web(monkeypatch):
    pages = {"https://acme.example/": PAGE_1}
    monkeypatch.setattr(cw, "fetch", lambda url: (200, pages[url]) if url in pages else (404, ""))
    return pages


@pytest.fixture
def google(monkeypatch):
    state = {"rating": 4.5, "count": 120, "reviews": []}

    def fake(method, path, fields, body=None):
        if path == "places:searchText":
            return 200, {"places": [{"id": "PLACE1"}]}
        return 200, {"id": "PLACE1", "displayName": {"text": "Acme Safaris"}, "rating": state["rating"], "userRatingCount": state["count"],
                     "googleMapsUri": "https://maps.google.com/?cid=1", "reviews": state["reviews"]}
    monkeypatch.setattr(cw, "_places", fake)
    save_credentials("google_places", {"api_key": "g-key"})
    return state


@pytest.fixture
def watcher(make_user):
    return make_user("ops@example.com", R.OPERATIONS)


def test_page_text_drops_scripts_and_keeps_lines():
    title, lines = cw.page_text(PAGE_1)
    assert title == "Acme Safaris" and "var x=1" not in " ".join(lines) and "3-day Mara from USD 450 pp" in lines


def test_website_change_is_found_and_watchers_are_alerted(web, watcher, make_user):
    seller = make_user("sm@example.com", R.SALES_MANAGER)
    agent = make_user("ag@example.com", R.SALES_AGENT)
    c = Competitor.objects.create(name="Acme Safaris", website="https://acme.example/")
    first = cw.check(c)[0]
    assert first.ok and not first.changed and "USD 450 pp" in " ".join(first.prices)
    web["https://acme.example/"] = PAGE_2
    second = cw.check(c)[0]
    assert second.changed and "NEW: Amboseli weekend KES 25,000" in second.added and "3-day Mara from USD 450 pp" in second.removed
    assert "KES 25,000" in second.prices
    for u in (watcher, seller):
        assert Notification.objects.filter(user=u, title="Acme Safaris made changes").exists()
    assert not Notification.objects.filter(user=agent).exists()
    assert cw.check(c)[0].changed is False                                     # nothing new the third time
    web.pop("https://acme.example/")
    bad = cw.check(c)[0]
    assert not bad.ok and "404" in bad.error


def test_google_profile_rating_and_new_reviews(web, google):
    c = Competitor.objects.create(name="Acme Safaris", google_query="https://www.google.com/maps/place/Acme+Safaris/@-1.2,36.8,17z")
    g = cw.check_google(c)
    c.refresh_from_db()
    assert c.google_place_id == "PLACE1" and float(g.rating) == 4.5 and g.review_count == 120 and g.new_reviews == []
    google.update(rating=4.6, count=122, reviews=[{"rating": 5, "text": {"text": "Loved the Mara"}, "authorAttribution": {"displayName": "Jo"},
                                                   "publishTime": (timezone.now() + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")}])
    g2 = cw.check_google(c)
    assert g2.changed and g2.review_count == 122 and g2.new_reviews[0]["text"] == "Loved the Mara"
    assert cw._query_from("https://www.google.com/maps/place/Acme+Safaris/@1,2", "x") == "Acme Safaris"


def test_pages_charts_and_access(client, web, google, watcher, make_user):
    client.force_login(make_user("ag2@example.com", R.SALES_AGENT))
    assert client.get(reverse("insights:competitors")).status_code == 403
    client.force_login(watcher)
    assert client.get(reverse("insights:competitors")).status_code == 200
    r = client.post(reverse("insights:competitor_new"), {"name": "Acme Safaris", "website": "https://acme.example/", "google_query": "Acme Safaris, Nairobi",
                                                        "is_main": "on", "is_active": "on"})
    c = Competitor.objects.get()
    assert r.status_code == 302 and c.snapshots.count() == 2 and c.created_by == watcher       # first look: website + Google
    web["https://acme.example/"] = PAGE_2
    client.post(reverse("insights:competitor", args=[c.pk]), {"action": "check"})
    page = client.get(reverse("insights:competitors")).content.decode()
    assert "Our greatest competitor" in page and "Acme Safaris" in page and 'data-chart-key="changes"' in page
    detail = client.get(reverse("insights:competitor", args=[c.pk])).content.decode()
    assert "NEW: Amboseli weekend KES 25,000" in detail and "KES 25,000" in detail
    assert cw.charts(c)["changes"]["values"][-1] == 1
    for u in (make_user("sm2@example.com", R.SALES_MANAGER), make_user("ceo@example.com", R.DIRECTOR), make_user("coo@example.com", R.COO)):
        client.force_login(u)
        assert client.get(reverse("insights:competitor", args=[c.pk])).status_code == 200
    client.post(reverse("insights:competitor", args=[c.pk]), {"action": "delete"})
    assert not Competitor.objects.exists() and not Snapshot.objects.exists()


def test_web_search_answers_with_sources(client, watcher, monkeypatch):
    from apps.ict import ai

    save_credentials("ai", {"api_key": "sk-ant-x"})
    calls = []

    def fake_post(payload):
        calls.append(payload)
        if len(calls) == 1:                                                    # a long search pauses once, then finishes
            return {"stop_reason": "pause_turn", "content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "mara"}}], "usage": {"input_tokens": 10, "output_tokens": 5}}
        return {"stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": 50}, "content": [
            {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [{"type": "web_search_result", "url": "https://a.example/mara", "title": "Mara deals"}]},
            {"type": "text", "text": "- Acme: 3-day Mara from USD 399", "citations": [{"type": "web_search_result_location", "url": "https://a.example/mara", "title": "Mara deals", "cited_text": "x"}]}]}
    monkeypatch.setattr(ai, "_post", fake_post)
    client.force_login(watcher)
    r = client.post(reverse("insights:competitors"), {"action": "search", "q": "best Mara safaris"})
    s = MarketSearch.objects.get()
    assert r.status_code == 302 and s.ok and "USD 399" in s.answer and s.sources == [{"url": "https://a.example/mara", "title": "Mara deals"}]
    assert calls[0]["tools"][0]["name"] == "web_search" and calls[1]["messages"][-1]["role"] == "assistant"
    page = client.get(reverse("insights:competitors") + f"?search={s.pk}").content.decode()
    assert "USD 399" in page and "https://a.example/mara" in page
    monkeypatch.setattr(ai, "_post", lambda p: (_ for _ in ()).throw(ai.AIUnavailable("no credit")))
    bad = cw.search_web(watcher, "anything")
    assert not bad.ok and "no credit" in bad.answer


def test_search_tool_version_follows_the_model():
    from apps.ict import ai

    assert ai._search_tool("claude-sonnet-5-5")["type"] == "web_search_20260209"
    assert ai._search_tool("claude-haiku-5-5")["type"] == "web_search_20250305"


# ------------------------------------------------------------------ weekend roster
def test_weekend_roster_add_alert_copy_and_attendance(client, make_user, django_capture_on_commit_callbacks):
    from apps.frontdesk import roster
    from apps.frontdesk.models import WeekendShift
    from apps.people.models import Employee

    manager = make_user("fdm@example.com", R.FRONTDESK_MANAGER)
    staff_user = make_user("rec@example.com", R.RECEPTIONIST, full_name="Rose Wanjiku")
    rose = Employee.objects.create(user=staff_user, full_name="Rose Wanjiku")
    sat, sun = roster.weekends()[1]                                             # next weekend
    client.force_login(staff_user)
    assert client.get(reverse("frontdesk:weekend_roster")).status_code == 200   # the desk can see it
    assert client.post(reverse("frontdesk:weekend_roster"), {"action": "add", "date": sat.isoformat(), "employees": [rose.pk]}).status_code == 403
    client.force_login(manager)
    client.post(reverse("frontdesk:weekend_roster"), {"action": "add", "date": sat.isoformat(), "employees": [rose.pk], "start_time": "08:00", "end_time": "13:00", "note": "reception"})
    shift = WeekendShift.objects.get()
    assert shift.date == sat and f"{shift.start_time:%H:%M}" == "08:00" and shift.note == "reception"
    assert Notification.objects.filter(user=staff_user, title__startswith="You're on the weekend roster").exists()
    monday = sat - timedelta(days=5)
    assert client.post(reverse("frontdesk:weekend_roster"), {"action": "add", "date": monday.isoformat(), "employees": [rose.pk]}).status_code == 302
    assert WeekendShift.objects.count() == 1                                    # weekdays are refused
    page = client.get(reverse("frontdesk:weekend_roster")).content.decode()
    assert "Rose Wanjiku" in page and "08:00–13:00" in page
    # her own My To-Do shows it
    client.force_login(staff_user)
    assert "on the weekend roster" in client.get("/system/todo/").content.decode()
    # copy to the weekend after
    client.force_login(manager)
    next_sat = sat + timedelta(days=7)
    client.post(reverse("frontdesk:weekend_roster"), {"action": "copy", "saturday": next_sat.isoformat()})
    assert WeekendShift.objects.filter(date=next_sat, employee=rose).exists()
    # Friday reminder
    from apps.frontdesk import tasks

    friday = sat - timedelta(days=1)
    monkey_today = friday
    import apps.frontdesk.tasks as t
    orig = t.timezone.localdate
    t.timezone.localdate = lambda: monkey_today
    try:
        assert tasks.weekend_roster_reminder() == "1 reminded"
    finally:
        t.timezone.localdate = orig
    # remove
    client.post(reverse("frontdesk:weekend_roster"), {"action": "remove", "pk": shift.pk})
    assert not WeekendShift.objects.filter(pk=shift.pk).exists()
    assert Notification.objects.filter(user=staff_user, title__startswith="Taken off").exists()
