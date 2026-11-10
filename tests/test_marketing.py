"""Marketing: lead ingestion, public capture + webhook security, tracked links, automation, ad spend → Finance."""
import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.core.cache import cache
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.catalog.models import TourPackage
from apps.finance.models import Expense
from apps.leads.models import Enquiry
from apps.marketing import services
from apps.marketing.models import (AdSpendDaily, AutomationWorkflow, Campaign, Channel, InboxMessage, LinkClick, Review,
                                   ReviewRequest, TrackedLink)
from apps.notifications.models import OutboundMessage
from apps.people.models import Employee
from apps.sales.models import SalesSettings


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield


@pytest.fixture
def marketer(make_user):
    return make_user("mkt@example.com", R.MARKETING)


@pytest.mark.django_db
def test_ingest_lead_creates_enquiry_for_the_front_desk_and_welcomes(agent):
    camp = Campaign.objects.create(name="Mara July", status="active")
    e = services.ingest_lead(name="Wanjiru Kamau", phone="0712345678", email="w@example.com", source="website",
                             message="July dates?", campaign=camp, adults=3)
    assert e.source == "website" and e.campaign == camp and e.assigned_agent is None and e.adults == 3     # lands at the Front Desk, not an agent
    msg = OutboundMessage.objects.get(purpose="welcome")
    assert msg.channel == "whatsapp" and msg.to == "+254712345678" and "Wanjiru" in msg.body and msg.status == "sent"
    assert AutomationWorkflow.objects.get(key="welcome").run_count == 1


@pytest.mark.django_db
def test_disabled_workflow_sends_nothing(agent):
    services.ensure_workflows()
    AutomationWorkflow.objects.filter(key="welcome").update(enabled=False)
    services.ingest_lead(name="A B", phone="0712345679", source="website")
    assert not OutboundMessage.objects.exists()


@pytest.mark.django_db
def test_returning_guest_is_linked_not_duplicated(agent):
    a = services.ingest_lead(name="Same Person", phone="0712345680", source="website")
    b = services.ingest_lead(name="Same Person", phone="+254712345680", source="call")
    assert a.client_id == b.client_id and b.is_returning_client


# ------------------------------------------------------------------------------------------------ public capture
@pytest.mark.django_db
def test_public_capture_form_creates_attributed_lead(client, agent):
    r = client.get("/capture/?event=Nairobi Travel Expo")
    assert r.status_code == 200 and b"Nairobi Travel Expo" in r.content
    r = client.post("/capture/", {"full_name": "Expo Visitor", "phone": "0722111222", "adults": 2, "event": "Nairobi Travel Expo",
                                  "message": "Interested in Mara"})
    assert r.status_code == 200 and b"Asante" in r.content
    e = Enquiry.objects.get(client__full_name="Expo Visitor")
    assert e.campaign.name == "Expo: Nairobi Travel Expo" and e.campaign.objective == "expo" and "[Nairobi Travel Expo]" in e.notes


@pytest.mark.django_db
def test_capture_honeypot_and_validation_and_rate_limit(client):
    base = {"full_name": "Bot", "phone": "0722111222", "adults": 1}
    client.post("/capture/", {**base, "website": "http://spam"})
    assert not Enquiry.objects.exists()                                  # silently ignored
    r = client.post("/capture/", {"full_name": "X", "phone": "12", "adults": 1})
    assert r.status_code == 200 and b"valid phone" in r.content and not Enquiry.objects.exists()
    codes = [client.post("/capture/", {**base, "full_name": f"P{i}", "phone": f"07221112{i:02d}"}).status_code for i in range(14)]
    assert codes[-1] == 429


# ------------------------------------------------------------------------------------------------ webhook
@pytest.mark.django_db
@override_settings(WEBHOOK_SECRET="s3cret")
def test_webhook_requires_secret_and_creates_enquiry(client, agent):
    url = reverse("marketing:webhook_enquiry")
    body = json.dumps({"name": "Web Guest", "phone": "0733000111", "message": "Hi", "adults": 4})
    assert client.get(url).status_code == 405
    assert client.post(url, body, content_type="application/json").status_code == 401
    assert client.post(url, body, content_type="application/json", HTTP_X_WEBHOOK_SECRET="wrong").status_code == 401
    assert client.post(url, "{bad", content_type="application/json", HTTP_X_WEBHOOK_SECRET="s3cret").status_code == 400
    assert client.post(url, json.dumps({"name": "x"}), content_type="application/json", HTTP_X_WEBHOOK_SECRET="s3cret").status_code == 400
    r = client.post(url, body, content_type="application/json", HTTP_X_WEBHOOK_SECRET="s3cret")
    assert r.status_code == 201 and r.json()["assigned"] is False          # waits at the Front Desk
    assert Enquiry.objects.get(client__full_name="Web Guest").adults == 4


@pytest.mark.django_db
@override_settings(WEBHOOK_SECRET="")
def test_webhook_disabled_without_secret(client):
    r = client.post(reverse("marketing:webhook_enquiry"), "{}", content_type="application/json")
    assert r.status_code == 401


# ------------------------------------------------------------------------------------------------ tracked links
@pytest.mark.django_db
def test_tracked_link_logs_click_sets_cookie_and_attributes_leads(client, agent):
    services.ensure_channels()
    camp = Campaign.objects.create(name="Mara Promo", status="active")
    link = TrackedLink.objects.create(slug="mara-ig", name="Mara IG", destination="https://pocketofparadise.co.ke/mara",
                                      campaign=camp, channel=Channel.objects.get(key="instagram"), medium="social")
    r = client.get("/l/mara-ig/")
    assert r.status_code == 302 and "utm_source=instagram" in r["Location"] and "utm_campaign=mara-promo" in r["Location"]
    assert LinkClick.objects.filter(link=link).count() == 1 and r.cookies["ds_link"].value == "mara-ig"
    client.post("/capture/", {"full_name": "From Link", "phone": "0744000111", "adults": 2})
    e = Enquiry.objects.get(client__full_name="From Link")
    assert e.tracked_link == link and e.campaign == camp
    assert client.get("/l/nope/").status_code == 404


# ------------------------------------------------------------------------------------------------ jobs / automation
@pytest.mark.django_db
def test_ad_spend_syncs_and_posts_expense_once(agent):
    services.ensure_channels()
    camp = Campaign.objects.create(name="Paid", status="active", start_date=timezone.localdate() - timedelta(days=5), budget=D("100000"))
    camp.channels.set(Channel.objects.filter(key__in=["facebook", "google"]))
    assert services.sync_ad_spend() == 2
    assert services.sync_ad_spend() == 2 and AdSpendDaily.objects.count() == 2          # idempotent per day
    assert services.post_ad_spend_to_finance() == 2
    assert services.post_ad_spend_to_finance() == 0
    exp = Expense.objects.filter(category="marketing")
    assert exp.count() == 2 and all(e.status == "paid" for e in exp)
    from apps.finance import services as fin
    from apps.finance.models import Account

    assert fin._bal(Account.objects.get(key="exp_marketing"), fin._balances()) == sum((e.amount for e in exp), D("0"))
    assert fin.balance_sheet(timezone.localdate())["balanced"]


@pytest.mark.django_db
def test_stats_reviews_and_inbox_sync_are_idempotent():
    services.ensure_channels()
    assert services.sync_channel_stats() == 6
    first = services.sync_reviews()
    assert services.sync_reviews() == 0
    n = services.sync_inbox()
    assert services.sync_inbox() == 0
    assert Review.objects.count() == first and InboxMessage.objects.count() == n


@pytest.mark.django_db
def test_unresponsive_leads_are_reassigned_once_and_manager_alerted(agent, other_agent, make_user):
    mgr = make_user("mgr@example.com", R.SALES_MANAGER)
    from apps.leads import services as lead_services
    from apps.leads.models import LeadAssignment
    from apps.notifications.models import Notification

    e = lead_services.create_enquiry(client_data={"full_name": "Slow", "phone": "0755000111"},
                                     enquiry_data={"source": "website"}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    LeadAssignment.objects.filter(enquiry=e).update(assigned_at=timezone.now() - timedelta(hours=2))
    Enquiry.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(hours=2))
    from apps.sales.models import SalesSettings

    SalesSettings.objects.update_or_create(pk=1, defaults={"auto_assign_new": False})
    assert services.reassign_unresponsive() == 0                      # routing is off (ICT switch): nothing moves on its own
    SalesSettings.objects.update_or_create(pk=1, defaults={"auto_assign_new": True})
    assert services.reassign_unresponsive() == 1
    e.refresh_from_db()
    assert e.assigned_agent == other_agent and Notification.objects.filter(user=mgr, title__contains="reassigned").exists()
    LeadAssignment.objects.filter(enquiry=e, active=True).update(assigned_at=timezone.now() - timedelta(hours=2))
    assert services.reassign_unresponsive() == 0                      # only once


@pytest.mark.django_db
def test_pretrip_and_review_workflows(agent):
    from apps.leads import services as lead_services
    from apps.sales import services as sales
    from apps.trips import services as trips
    from apps.trips.models import Departure

    pkg = TourPackage.objects.create(title="Mara", days=3, nights=2, currency="KES", price_per_person=D("50000"), status="published")
    e = lead_services.create_enquiry(client_data={"full_name": "Trip Guest", "phone": "0766000111"},
                                     enquiry_data={"source": "call", "package": pkg, "adults": 1}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    q = sales.create_quotation(e, agent.user, currency="KES", items=[{"kind": "other", "description": "T", "quantity": 1, "unit_price": 50000}])
    sales.send_quotation(q, "email", agent.user)
    b = sales.accept_quotation(q, agent.user)
    dep = Departure.objects.create(package=pkg, start_date=timezone.localdate() + timedelta(days=7), end_date=timezone.localdate() + timedelta(days=9))
    trips.attach_booking(b, dep)
    assert services.send_pretrip_reminders() == 1
    assert services.send_pretrip_reminders() == 0                      # no repeat for the same day
    assert OutboundMessage.objects.filter(purpose="pretrip", to="+254766000111").exists()
    services.on_departure_completed(dep)
    assert ReviewRequest.objects.filter(booking=b).count() == 1
    services.on_departure_completed(dep)
    assert ReviewRequest.objects.filter(booking=b).count() == 1 and OutboundMessage.objects.filter(purpose="review_request").count() == 1
    assert dep.posts.filter(source="trip_completed").count() == 1    # content draft created once


@pytest.mark.django_db
def test_convert_inbox_message_to_enquiry(agent):
    services.ensure_channels()
    m = InboxMessage.objects.create(channel=Channel.objects.get(key="instagram"), sender="Insta User", message="Price for Diani?", external_id="x1")
    with pytest.raises(ValueError):
        services.convert_to_enquiry(m)
    enq = services.convert_to_enquiry(m, None, "0777000111")
    assert enq.source == "instagram" and "Diani" in enq.notes and services.convert_to_enquiry(m).pk == enq.pk


# ------------------------------------------------------------------------------------------------ analytics & screens
@pytest.mark.django_db
def test_attribution_numbers(agent):
    services.ensure_channels()
    camp = Campaign.objects.create(name="C", status="active", start_date=timezone.localdate())
    camp.channels.set(Channel.objects.filter(key="facebook"))
    AdSpendDaily.objects.create(campaign=camp, channel=Channel.objects.get(key="facebook"), date=timezone.localdate(),
                                spend=D("10000"), impressions=10000, clicks=200)
    for i in range(4):
        services.ingest_lead(name=f"L{i}", phone=f"07880001{i:02d}", source="facebook", campaign=camp)
    today = timezone.localdate()
    s = services.spend_summary(today, today)
    assert s["enquiries"] == 4 and s["cpl"] == 2500 and s["ctr"] == 2.0
    comp = services.channel_comparison(today, today)
    assert comp[0]["channel"].key == "facebook" and comp[0]["enquiries"] == 4


@pytest.mark.django_db
def test_marketing_access_and_every_screen(client, agent, marketer):
    services.ensure_channels()
    services.ingest_lead(name="Seen", phone="0799000111", source="facebook")
    services.sync_channel_stats(); services.sync_reviews(); services.sync_inbox()
    camp = Campaign.objects.create(name="Camp", status="active")
    link = TrackedLink.objects.create(slug="x", name="X", destination="https://e.com", campaign=camp)
    from apps.marketing.models import Post

    post = Post.objects.create(title="P", status="needs_approval", scheduled_at=timezone.now())
    client.force_login(agent.user)
    assert client.get("/marketing/").status_code == 403
    client.force_login(marketer)
    urls = ["/marketing/", "/marketing/?channel=facebook&range=year", "/marketing/social/", "/marketing/channels/facebook/",
            "/marketing/inbox/", "/marketing/inbox/?filter=all", "/marketing/enquiries/", "/marketing/campaigns/",
            "/marketing/campaigns/?export=csv", camp.get_absolute_url(), "/marketing/campaigns/new/", f"/marketing/campaigns/{camp.pk}/edit/",
            "/marketing/content/", "/marketing/posts/", post.get_absolute_url(), "/marketing/posts/new/", "/marketing/media/",
            "/marketing/reviews/", "/marketing/reviews/?export=csv", "/marketing/analytics/", "/marketing/accounts/",
            "/marketing/links/", f"/marketing/links/{link.pk}/", "/marketing/lead-quality/", "/marketing/automation/",
            "/marketing/integrations/", "/capture/"]
    for u in urls:
        r = client.get(u)
        assert r.status_code == 200, (u, r.status_code)
        if "text/html" in r["Content-Type"]:
            assert b"{{" not in r.content and b"{%" not in r.content, u
    assert client.get("/marketing/assignment/").status_code == 403        # marketing never distributes leads
    client.post(reverse("marketing:post_action", args=[post.pk]), {"action": "approve"})
    post.refresh_from_db()
    assert post.status == "scheduled" and post.approved_by == marketer
    client.post(reverse("marketing:campaign_action", args=[camp.pk]), {"action": "pause"})
    camp.refresh_from_db()
    assert camp.status == "paused"


@pytest.mark.django_db
def test_posting_calendar_schedule_and_drag(client, make_user):
    from datetime import timedelta

    from django.utils import timezone

    from apps.accounts import roles as R
    from apps.marketing.models import Channel, Post

    ch, _ = Channel.objects.get_or_create(key="instagram", defaults={"name": "Instagram"})
    mk = make_user("mk@example.com", R.MARKETING)
    client.force_login(mk)
    day = timezone.localdate() + timedelta(days=3)
    html = client.get("/marketing/content/").content.decode()
    assert "Posting Calendar" in html and f'data-day="{day:%Y-%m-%d}"' in html and "data-modal-form" in html
    frag = client.get(f"/marketing/posts/new/?date={day:%Y-%m-%d}&from=calendar", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert frag.status_code == 200 and f"{day:%Y-%m-%d}T10:00" in frag.content.decode()               # that day at 10:00, status Scheduled
    ok = client.post(f"/marketing/posts/new/?date={day:%Y-%m-%d}&from=calendar", {"title": "Reel: Mara sunrise", "caption": "x", "channels": [ch.pk], "format": "reel",
                                                                                      "scheduled_at": f"{day:%Y-%m-%d}T10:00", "status": "scheduled"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    p = Post.objects.get(title="Reel: Mara sunrise")
    assert ok.json()["ok"] and "/marketing/content/" in ok.json()["redirect"] and p.status == "scheduled"
    new_day = day + timedelta(days=2)
    assert client.post(f"/marketing/posts/{p.pk}/reschedule/", {"date": f"{new_day:%Y-%m-%d}"}).json()["ok"]
    p.refresh_from_db()
    assert timezone.localtime(p.scheduled_at).date() == new_day and timezone.localtime(p.scheduled_at).hour == 10    # time of day kept
    draft = Post.objects.create(title="Draft idea", status="draft")
    assert client.post(f"/marketing/posts/{draft.pk}/reschedule/", {"date": f"{day:%Y-%m-%d}"}).json()["ok"]
    draft.refresh_from_db()
    assert draft.status == "scheduled" and draft.scheduled_at
    done = Post.objects.create(title="Old", status="published", scheduled_at=timezone.now())
    assert client.post(f"/marketing/posts/{done.pk}/reschedule/", {"date": f"{day:%Y-%m-%d}"}).status_code == 400
    assert client.post(f"/marketing/posts/{p.pk}/reschedule/", {"date": "2001-01-01"}).status_code == 400
    sales = make_user("s@example.com", R.SALES_AGENT)
    client.force_login(sales)
    assert client.post(f"/marketing/posts/{p.pk}/reschedule/", {"date": f"{day:%Y-%m-%d}"}).status_code == 403
