"""Phase 3: sales module access rules, pipeline, quotations, bookings, assignment and analytics."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.accounts.models import User
from apps.leads import services as lead_services
from apps.leads.models import Enquiry, Stage
from apps.people.models import Employee
from apps.sales import analytics, services
from apps.sales.models import (AssignmentRule, Booking, BookingStatus, CallLog, FollowUp, Quotation, QuotationStatus,
                               SalesSettings)


def _enquiry(agent=None, phone="0712345678", name="Wanjiru Kamau", **kw):
    e = lead_services.create_enquiry(
        client_data={"full_name": name, "phone": phone}, enquiry_data={"source": "whatsapp", **kw}, auto_assign=False)
    if agent:
        lead_services.assign_agent(e, agent, None, "manual")
    return e


@pytest.fixture
def manager(make_user):
    u = make_user("mgr@example.com", R.SALES_MANAGER, full_name="Joyce Nduta")
    Employee.objects.create(user=u, full_name="Joyce Nduta")
    return u


@pytest.fixture
def agent_client(client, agent):
    client.force_login(agent.user)
    return client


# ------------------------------------------------------------------------------------- access: sales sees only sales
@pytest.mark.django_db
def test_sales_roles_only_get_the_sales_module(agent, manager):
    from apps.core.modules import modules_for

    for user in (agent.user, manager):
        assert [m.key for m in modules_for(user)]  == ["sales", "me"]      # their own module + My Space; KYC, signatures and chats live inside Sales


@pytest.mark.django_db
def test_sales_agent_blocked_from_other_modules(agent_client):
    for url in ("/operations/", "/fleet/", "/staff/", "/frontdesk/", "/system/status/", "/accounts/users/",
                "/catalog/accommodations/", "/clients/new/", "/enquiries/new/x/"):
        assert agent_client.get(url).status_code in (403, 404), url
    for url in ("/sales/", "/sales/pipeline/", "/enquiries/", "/clients/", "/catalog/packages/"):
        assert agent_client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_agent_cannot_open_manager_screens(agent_client):
    for url in ("/sales/team/", "/sales/assignment/", "/sales/targets/", "/sales/reports/"):
        assert agent_client.get(url).status_code == 403, url


@pytest.mark.django_db
def test_agent_sees_only_own_leads_clients_and_quotes(agent, other_agent, agent_client):
    mine = _enquiry(agent, "0711000001", "Mine Client")
    theirs = _enquiry(other_agent, "0711000002", "Theirs Client")
    q_theirs = services.create_quotation(theirs, other_agent.user, items=[
        {"kind": "other", "description": "x", "quantity": 1, "unit_price": 100}])
    assert agent_client.get(mine.get_absolute_url()).status_code == 200
    assert agent_client.get(theirs.get_absolute_url()).status_code == 403
    assert agent_client.get(theirs.client.get_absolute_url()).status_code == 404
    assert agent_client.get(q_theirs.get_absolute_url()).status_code == 404
    body = agent_client.get("/sales/pipeline/").content.decode()
    assert "Mine Client" in body and "Theirs Client" not in body
    assert "Theirs Client" not in agent_client.get("/clients/").content.decode()
    assert "Theirs Client" not in agent_client.get("/search/?q=Theirs").content.decode()


@pytest.mark.django_db
def test_agent_cannot_act_on_someone_elses_lead(agent, other_agent, agent_client):
    theirs = _enquiry(other_agent, "0711000002", "Theirs Client")
    for url in (f"/sales/calls/new/?enquiry={theirs.pk}", f"/sales/quotations/new/?enquiry={theirs.pk}"):
        assert agent_client.get(url).status_code in (403, 404)
    r = agent_client.post(reverse("sales:board_move", args=[theirs.pk]), {"stage": "hot"})
    assert r.status_code in (403, 404)
    theirs.refresh_from_db()
    assert theirs.stage == Stage.ENQUIRY


@pytest.mark.django_db
def test_manager_sees_every_agent_and_director_is_read_only(agent, other_agent, manager, client, make_user):
    _enquiry(agent, "0711000001", "Mine Client")
    _enquiry(other_agent, "0711000002", "Theirs Client")
    client.force_login(manager)
    body = client.get("/sales/pipeline/").content.decode()
    assert "Mine Client" in body and "Theirs Client" in body
    assert client.get("/sales/team/").status_code == 200
    assert client.get(reverse("sales:team_agent", args=[agent.pk])).status_code == 200
    director = make_user("dir@example.com", R.DIRECTOR)
    client.force_login(director)
    assert client.get("/sales/team/").status_code == 200
    assert client.post(reverse("sales:assignment_auto")).status_code == 302       # the CEO may distribute leads, but nothing else here
    e = Enquiry.objects.first()
    assert client.get(f"/sales/calls/new/?enquiry={e.pk}").status_code == 403
    assert client.post(f"/sales/pipeline/{e.pk}/move/", {"stage": "hot"}).status_code == 403


# ------------------------------------------------------------------------------------- quotations & bookings
@pytest.mark.django_db
def test_quote_to_booking_flow_moves_the_pipeline(agent):
    e = _enquiry(agent, adults=2)
    q = services.create_quotation(e, agent.user, currency="KES", items=[
        {"kind": "other", "description": "Mara 3 days", "quantity": 2, "unit_price": 50000}])
    assert q.reference.startswith("QT-") and q.total == Decimal("100000.00") + q.vat_amount
    with pytest.raises(ValueError):
        services.accept_quotation(q, agent.user)            # must be sent first
    services.send_quotation(q, "whatsapp", agent.user)
    e.refresh_from_db()
    assert e.stage == Stage.QUOTED and e.quotation_status == "quoted" and e.deal_value == q.total_kes
    booking = services.accept_quotation(q, agent.user, deposit_percent=30)
    e.refresh_from_db()
    assert e.stage == Stage.ITINERARY and booking.status == BookingStatus.PENDING
    assert booking.deposit_amount == (q.total * Decimal("0.30")).quantize(Decimal("0.01"))
    services.record_payment(booking, agent.user, amount=booking.deposit_amount, method="mpesa", reference="SKL123")
    booking.refresh_from_db(); e.refresh_from_db()
    assert booking.status == BookingStatus.DEPOSIT and e.stage == Stage.DEPOSIT
    services.record_payment(booking, agent.user, amount=booking.balance)
    booking.refresh_from_db(); e.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED and e.stage == Stage.CONFIRMED and booking.balance == 0
    services.set_booking_status(booking, BookingStatus.COMPLETED, agent.user)
    e.refresh_from_db()
    assert e.stage == Stage.COMPLETED


@pytest.mark.django_db
def test_payment_rules(agent):
    e = _enquiry(agent)
    q = services.create_quotation(e, agent.user, currency="USD", items=[
        {"kind": "other", "description": "Trip", "quantity": 1, "unit_price": 1000}])
    services.send_quotation(q, "email", agent.user)
    b = services.accept_quotation(q, agent.user)
    with pytest.raises(ValueError):
        services.record_payment(b, agent.user, amount=5000)       # more than the balance
    with pytest.raises(ValueError):
        services.record_payment(b, agent.user, amount=0)
    with pytest.raises(ValueError):
        services.set_booking_status(b, BookingStatus.CONFIRMED, agent.user)   # no deposit yet
    with pytest.raises(ValueError):
        services.set_booking_status(b, BookingStatus.CANCELLED, agent.user)   # reason required
    services.set_booking_status(b, BookingStatus.CANCELLED, agent.user, "Client postponed")
    e.refresh_from_db()
    assert e.stage == Stage.LOST and "postponed" in e.lost_reason


@pytest.mark.django_db
def test_revising_creates_new_version_and_supersedes(agent):
    e = _enquiry(agent)
    q1 = services.create_quotation(e, agent.user, currency="KES", items=[
        {"kind": "other", "description": "Trip", "quantity": 1, "unit_price": 80000}])
    services.send_quotation(q1, "whatsapp", agent.user)
    q2 = services.revise_quotation(q1, agent.user)
    assert q2.version == 2 and q2.status == QuotationStatus.DRAFT and q2.items.count() == 1
    services.send_quotation(q2, "whatsapp", agent.user)
    q1.refresh_from_db(); e.refresh_from_db()
    assert q1.status == QuotationStatus.SUPERSEDED and e.quotation_status == "revised"
    with pytest.raises(ValueError):
        services.send_quotation(q1, "email", agent.user)


@pytest.mark.django_db
def test_cannot_send_empty_quotation(agent):
    q = services.create_quotation(_enquiry(agent), agent.user)
    with pytest.raises(ValueError):
        services.send_quotation(q, "email", agent.user)


@pytest.mark.django_db
def test_quotation_views_end_to_end(agent, agent_client):
    e = _enquiry(agent)
    # a quotation is made from an itinerary: without one the agent is sent to build it
    assert "/sales/itineraries/new/" in agent_client.get(f"/sales/quotations/new/?enquiry={e.pk}")["Location"]
    it = services.create_itinerary(e, agent.user)
    from apps.sales.models import ItineraryPriceLine
    ItineraryPriceLine.objects.create(itinerary=it, description="Safari package", quantity=1, unit_price=50000)
    r = agent_client.get(f"/sales/quotations/new/?enquiry={e.pk}")
    assert r.status_code == 302
    q = Quotation.objects.get(enquiry=e)
    q.items.all().delete()                      # the test prices the quotation by hand below
    assert agent_client.get(r.url).status_code == 200
    data = {"title": "Mara", "currency": "KES", "valid_until": (timezone.localdate() + timedelta(days=5)).isoformat(),
            "discount": "0", "vat_rate": "16", "intro": "", "terms": "t",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "1000",
            "items-0-kind": "other", "items-0-description": "Package", "items-0-quantity": "2",
            "items-0-unit_price": "10000"}
    data = {k.replace("items-", "items-"): v for k, v in data.items()}
    r = agent_client.post(r.url, data)
    q.refresh_from_db()
    assert r.status_code == 302 and q.subtotal == Decimal("20000.00") and q.total == Decimal("23200.00")
    assert agent_client.get(q.get_absolute_url()).status_code == 200
    assert agent_client.get(reverse("sales:quotation_print", args=[q.pk])).status_code == 200
    assert agent_client.get(reverse("sales:quotation_pdf", args=[q.pk])).status_code in (200, 302)
    agent_client.post(reverse("sales:quotation_send", args=[q.pk]), {"via": "whatsapp"})
    q.refresh_from_db()
    assert q.status == QuotationStatus.SENT
    r = agent_client.post(reverse("sales:quotation_accept", args=[q.pk]), {"deposit_percent": 30})
    b = Booking.objects.get(enquiry=e)
    assert r.url == b.get_absolute_url() and agent_client.get(r.url).status_code == 200
    r = agent_client.post(reverse("sales:booking_payment", args=[b.pk]),
                          {"amount": str(b.deposit_amount), "method": "mpesa", "reference": "SKL1"})
    assert r.status_code == 302
    b.refresh_from_db()
    assert b.status == BookingStatus.DEPOSIT


@pytest.mark.django_db
def test_call_log_updates_contact_and_creates_followup(agent, agent_client):
    e = _enquiry(agent)
    due = (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    r = agent_client.post(f"/sales/calls/new/?enquiry={e.pk}", {
        "enquiry": e.pk, "direction": "out", "outcome": "interested", "minutes": "4",
        "called_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "notes": "Wants Mara in July",
        "follow_up_at": due, "follow_up_title": "Send quote"})
    assert r.status_code == 302
    e.refresh_from_db()
    call = CallLog.objects.get(enquiry=e)
    assert call.duration_seconds == 240 and e.last_contact_at and e.next_action == "Send quote"
    fu = FollowUp.objects.get(enquiry=e)
    agent_client.post(reverse("sales:followup_done", args=[fu.pk]))
    fu.refresh_from_db(); e.refresh_from_db()
    assert fu.done_at and e.next_action == ""


@pytest.mark.django_db
def test_future_call_rejected(agent, agent_client):
    e = _enquiry(agent)
    future = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    r = agent_client.post(f"/sales/calls/new/?enquiry={e.pk}", {"enquiry": e.pk, "direction": "out",
                                                               "outcome": "connected", "called_at": future})
    assert r.status_code == 200 and not CallLog.objects.exists()


# ------------------------------------------------------------------------------------- board
@pytest.mark.django_db
def test_board_move_rules(agent, agent_client):
    e = _enquiry(agent)
    url = reverse("sales:board_move", args=[e.pk])
    assert agent_client.post(url, {"stage": "hot"}).status_code == 409        # no quotation yet: the user is asked to create one
    Quotation.objects.create(enquiry=e, title="T", currency="KES", fx_rate=1)
    assert agent_client.post(url, {"stage": "hot"}).json()["ok"]
    e.refresh_from_db(); assert e.stage == Stage.HOT
    assert agent_client.post(url, {"stage": "deposit"}).status_code == 400   # only via a payment
    assert agent_client.post(url, {"stage": "lost"}).status_code == 400      # needs a reason
    assert agent_client.post(url, {"stage": "lost", "reason": "Price"}).json()["ok"]
    assert agent_client.post(url, {"stage": "nope"}).status_code == 400
    assert agent_client.get(url).status_code == 405


# ------------------------------------------------------------------------------------- assignment
@pytest.mark.django_db
def test_round_robin_rotates_and_skips_unavailable(agent, other_agent):
    third = Employee.objects.create(full_name="Off Duty", is_sales_agent=True, on_duty=Employee.OFF)
    got = []
    for i in range(4):
        e = _enquiry(phone=f"07110000{i:02d}", name=f"C{i}")
        a, why = services.auto_assign(e, None)
        got.append(a.pk)
        assert why == "Round robin"
    assert third.pk not in got
    assert got[0] != got[1] and got[0] == got[2] and got[1] == got[3]


@pytest.mark.django_db
def test_rules_win_over_round_robin_and_respect_availability(agent, other_agent):
    AssignmentRule.objects.create(name="Intl", priority=1, international_only=True, agent=other_agent)
    e = lead_services.create_enquiry(client_data={"full_name": "Lukas", "phone": "+491512345678"},
                                     enquiry_data={"source": "website"}, auto_assign=False)
    a, why = services.auto_assign(e, None)
    assert a == other_agent and why == "Intl"
    other_agent.on_duty = Employee.OFF
    other_agent.save()
    e2 = lead_services.create_enquiry(client_data={"full_name": "Hans", "phone": "+491512345999"},
                                      enquiry_data={"source": "website"}, auto_assign=False)
    assert services.auto_assign(e2, None)[0] == agent


@pytest.mark.django_db
def test_daily_limit_stops_assignment(agent):
    agent.daily_enquiry_limit = 1
    agent.save()
    services.auto_assign(_enquiry(phone="0711000001", name="A"), None)
    with pytest.raises(ValueError):
        services.auto_assign(_enquiry(phone="0711000002", name="B"), None)


@pytest.mark.django_db(transaction=True)
def test_new_enquiries_auto_assigned_when_enabled(agent):
    SalesSettings.objects.update_or_create(pk=1, defaults={"auto_assign_new": True})
    e = lead_services.create_enquiry(client_data={"full_name": "Auto", "phone": "0733000001"},
                                     enquiry_data={"source": "whatsapp"})
    e.refresh_from_db()
    assert e.assigned_agent == agent


@pytest.mark.django_db
def test_sla_breach_and_going_cold(agent):
    e = _enquiry(agent)
    Enquiry.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(hours=3))
    from apps.leads.models import LeadAssignment

    LeadAssignment.objects.filter(enquiry=e).update(assigned_at=timezone.now() - timedelta(hours=2))
    assert services.sla_breaches().filter(pk=e.pk).exists()
    services.log_call(e, agent.user, outcome="connected")
    assert not services.sla_breaches().filter(pk=e.pk).exists()


# ------------------------------------------------------------------------------------- analytics
@pytest.mark.django_db
def test_agent_rows_aggregate_revenue_and_conversion(agent, other_agent):
    e = _enquiry(agent)
    q = services.create_quotation(e, agent.user, currency="KES", items=[
        {"kind": "other", "description": "T", "quantity": 1, "unit_price": 100000}])
    services.send_quotation(q, "email", agent.user)
    services.accept_quotation(q, agent.user)
    _enquiry(other_agent, "0711000009", "Other")
    today = timezone.localdate()
    rows = {r["agent"].pk: r for r in analytics.agent_rows(today, today)}
    assert rows[agent.pk]["won"] == 1 and rows[agent.pk]["revenue"] == q.total_kes
    assert rows[agent.pk]["quotes"] == 1 and rows[agent.pk]["conversion"] == 100
    assert rows[other_agent.pk]["won"] == 0 and rows[other_agent.pk]["open"] == 1
    f = analytics.funnel(Enquiry.objects.all(), today, today)
    assert [s["count"] for s in f][:1] == [2] and f[3]["count"] == 1


# ------------------------------------------------------------------------------------- seeded smoke
@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("seed_demo", "--reset", verbosity=0)
        yield
        call_command("flush", "--no-input", verbosity=0)


def _sales_urls():
    b, q = Booking.objects.first(), Quotation.objects.first()
    a = Employee.objects.filter(is_sales_agent=True).first()
    return ["/sales/", "/sales/?range=year", "/sales/pipeline/", "/sales/pipeline/?agent=none&source=whatsapp&q=a",
            "/sales/calls/", "/sales/calls/?outcome=connected", "/sales/calls/?export=csv", "/sales/followups/",
            "/sales/quotations/", "/sales/quotations/?export=xlsx", q.get_absolute_url(),
            reverse("sales:quotation_print", args=[q.pk]), "/sales/bookings/", "/sales/bookings/?export=csv",
            b.get_absolute_url(), reverse("sales:booking_edit", args=[b.pk]), "/sales/me/", "/sales/team/",
            "/sales/team/?export=csv", reverse("sales:team_agent", args=[a.pk]), "/sales/assignment/",
            "/sales/assignment/rules/new/", "/sales/targets/", "/sales/reports/", "/sales/reports/?export=sources",
            "/sales/calls/new/", "/sales/followups/new/", "/", "/enquiries/"]


@pytest.mark.django_db
@pytest.mark.parametrize("email", ["admin", "salesmanager", "director", "sarah", "mercy"])
def test_every_sales_screen_renders(client, seeded, email):
    client.force_login(User.objects.get(email=f"{email}@pocketofparadise.co.ke"))
    for url in _sales_urls():
        r = client.get(url)
        assert r.status_code in (200, 302, 403, 404), (url, r.status_code)
        if r.status_code == 200 and "text/html" in r["Content-Type"]:
            assert b"{{" not in r.content and b"{%" not in r.content, url


@pytest.mark.django_db
def test_manager_screens_all_200(client, seeded):
    client.force_login(User.objects.get(email="salesmanager@pocketofparadise.co.ke"))
    for url in _sales_urls():
        if url in ("/sales/me/",):
            continue
        assert client.get(url).status_code == 200, url


@pytest.mark.django_db
def test_seeded_agent_isolation(client, seeded):
    sarah = User.objects.get(email="sarah@pocketofparadise.co.ke")
    client.force_login(sarah)
    other = Booking.objects.exclude(agent=sarah.employee).first()
    assert client.get(other.get_absolute_url()).status_code == 404
    mine = Booking.objects.filter(agent=sarah.employee).first()
    if mine:
        assert client.get(mine.get_absolute_url()).status_code == 200
    assert sarah.employee.pk in [r["agent"].pk for r in analytics.agent_rows(*analytics.period("year")[:2])]
    body = client.get("/sales/").content.decode()
    assert "Agent leaderboard" not in body

