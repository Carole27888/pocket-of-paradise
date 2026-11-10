"""Trip readiness: resource rules, stage transitions, manifest and check-in, scoping."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.catalog.models import TourPackage
from apps.fleet.models import Vehicle
from apps.leads import services as lead_services
from apps.people.models import Employee
from apps.sales import services as sales
from apps.sales.models import BookingStatus
from apps.trips import services
from apps.trips.models import Departure, Stage


@pytest.fixture
def pkg(db):
    return TourPackage.objects.create(title="Mara 3 days", days=3, nights=2, currency="KES",
                                      price_per_person=Decimal("50000"), status="published")


@pytest.fixture
def crew(db):
    guide = Employee.objects.create(full_name="Guide One", is_guide=True, is_driver=True)
    driver = Employee.objects.create(full_name="Driver Two", is_driver=True)
    vehicle = Vehicle.objects.create(registration_no="KAA 001A", seats=7)
    return guide, driver, vehicle


def _dep(pkg, days=10, **kw):
    start = timezone.localdate() + timedelta(days=days)
    return Departure.objects.create(package=pkg, start_date=start, end_date=start + timedelta(days=2), **kw)


def _booking(agent, pkg, dep=None, adults=2, phone="0712000001"):
    e = lead_services.create_enquiry(client_data={"full_name": "Wanjiru Kamau", "phone": phone},
                                     enquiry_data={"source": "whatsapp", "package": pkg, "adults": adults},
                                     auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    q = sales.create_quotation(e, agent.user, currency="KES", items=[
        {"kind": "other", "description": "Trip", "quantity": adults, "unit_price": 50000}])
    sales.send_quotation(q, "email", agent.user)
    b = sales.accept_quotation(q, agent.user)
    if dep:
        services.attach_booking(b, dep)
    return b


def _complete_checklist(dep):
    services.set_checklist(dep, None, accommodation_confirmed=True, permits_issued=True, park_fees_paid=True,
                           meals_arranged=True, insurance_ok=True)


@pytest.mark.django_db
def test_reference_and_default_end_date(pkg):
    dep = Departure.objects.create(package=pkg, start_date=timezone.localdate(), end_date=timezone.localdate())
    assert dep.reference == "DEP-0001"


@pytest.mark.django_db
def test_off_duty_and_double_booked_resources_rejected(pkg, crew):
    guide, driver, vehicle = crew
    a, b = _dep(pkg, 10), _dep(pkg, 11)               # overlapping dates
    services.assign_resources(a, guide=guide, driver=driver, vehicle=vehicle)
    with pytest.raises(ValueError, match="already on"):
        services.assign_resources(b, guide=guide)
    with pytest.raises(ValueError, match="already on"):
        services.assign_resources(b, vehicle=vehicle)
    guide2 = Employee.objects.create(full_name="Off Guide", is_guide=True, on_duty=Employee.OFF)
    with pytest.raises(ValueError, match="off"):
        services.assign_resources(b, guide=guide2)
    vehicle.status = Vehicle.MAINTENANCE
    vehicle.save()
    with pytest.raises(ValueError, match="maintenance"):
        services.assign_resources(_dep(pkg, 40), vehicle=vehicle)


@pytest.mark.django_db
def test_stage_flow_requires_each_prerequisite(pkg, crew, agent):
    guide, driver, vehicle = crew
    dep = _dep(pkg, 3)
    with pytest.raises(ValueError, match="missing"):
        services.mark_ready(dep)
    services.assign_resources(dep, guide=guide, driver=driver, vehicle=vehicle)
    _complete_checklist(dep)
    services.mark_ready(dep, None)
    assert dep.stage == Stage.READY and dep.resources_confirmed_at
    with pytest.raises(ValueError, match="manifest"):
        services.mark_departed(dep)
    b = _booking(agent, pkg, dep)
    assert dep.manifest.count() == 2                   # lead guest + one placeholder
    services.mark_departed(dep)
    vehicle.refresh_from_db(); guide.refresh_from_db()
    assert dep.stage == Stage.DEPARTED and vehicle.status == Vehicle.OUT and guide.on_duty == Employee.OUT_ON_TRIP
    with pytest.raises(ValueError):
        services.assign_resources(dep, vehicle=vehicle)
    services.mark_completed(dep)
    vehicle.refresh_from_db(); b.refresh_from_db()
    assert dep.stage == Stage.COMPLETED and vehicle.status == Vehicle.AVAILABLE
    assert b.status == BookingStatus.PENDING          # only confirmed bookings are completed
    assert dep.events.count() >= 5


@pytest.mark.django_db
def test_completed_confirms_booking_to_completed_and_stage(pkg, crew, agent):
    guide, driver, vehicle = crew
    dep = _dep(pkg, 2)
    b = _booking(agent, pkg, dep)
    sales.record_payment(b, agent.user, amount=b.balance)           # fully paid → confirmed
    services.assign_resources(dep, guide=guide, driver=driver, vehicle=vehicle)
    _complete_checklist(dep)
    services.mark_ready(dep); services.mark_departed(dep); services.mark_completed(dep)
    b.refresh_from_db(); b.enquiry.refresh_from_db()
    assert b.status == BookingStatus.COMPLETED and b.enquiry.stage == "completed"


@pytest.mark.django_db
def test_capacity_enforced_and_cancel_releases_bookings(pkg, agent):
    dep = _dep(pkg, 10, capacity=3)
    b1 = _booking(agent, pkg, dep, adults=2)
    assert dep.seats_sold == 2 and dep.seats_left == 1
    e2 = lead_services.create_enquiry(client_data={"full_name": "Other", "phone": "0712000002"},
                                      enquiry_data={"source": "call", "package": pkg, "adults": 2}, auto_assign=False)
    lead_services.assign_agent(e2, agent, None, "manual")
    q = sales.create_quotation(e2, agent.user, currency="KES", items=[{"kind": "other", "description": "T", "quantity": 2, "unit_price": 1}])
    sales.send_quotation(q, "email", agent.user)
    b2 = sales.accept_quotation(q, agent.user)
    with pytest.raises(ValueError, match="seat"):
        services.attach_booking(b2, dep)
    with pytest.raises(ValueError):
        services.cancel(dep, None, "")
    services.cancel(dep, None, "Not enough guests")
    b1.refresh_from_db()
    assert dep.stage == Stage.CANCELLED and b1.departure is None


@pytest.mark.django_db
def test_checkin_and_incident_block_completion(pkg, crew, agent):
    guide, driver, vehicle = crew
    dep = _dep(pkg, 1)
    _booking(agent, pkg, dep)
    services.assign_resources(dep, guide=guide, driver=driver, vehicle=vehicle)
    _complete_checklist(dep)
    services.mark_ready(dep); services.mark_departed(dep)
    entry = dep.manifest.first()
    services.check_in(entry, agent.user)
    assert services.check_in_all(dep, agent.user) == dep.manifest.count() - 1
    inc = services.report_incident(dep, agent.user, kind="vehicle", severity="high", description="Flat tyre in the park")
    with pytest.raises(ValueError, match="incident"):
        services.mark_completed(dep)
    services.resolve_incident(inc, agent.user, "Fixed")
    services.mark_completed(dep)


@pytest.mark.django_db
def test_attention_reasons(pkg):
    dep = _dep(pkg, 2)
    reasons = dep.attention()
    assert any("Missing" in r for r in reasons) and any("manifest" in r.lower() for r in reasons)
    assert _dep(pkg, 40).attention() == []


@pytest.mark.django_db
def test_guide_sees_only_own_departures(client, make_user, pkg, crew):
    guide, driver, vehicle = crew
    u = make_user("guide@example.com", R.GUIDE)
    guide.user = u
    guide.save()
    mine, other = _dep(pkg, 5, guide=guide), _dep(pkg, 20)
    client.force_login(u)
    assert client.get(mine.get_absolute_url()).status_code == 200
    assert client.get(other.get_absolute_url()).status_code == 404
    assert client.get("/trips/").status_code == 200 and client.get("/trips/reports/").status_code == 403
    assert client.post(reverse("trips:advance", args=[mine.pk]), {"action": "ready"}).status_code == 403
    assert "DEP-0002" not in client.get("/trips/departures/").content.decode()


@pytest.mark.django_db
def test_manager_screens_render(client, make_user, pkg, crew, agent):
    guide, driver, vehicle = crew
    dep = _dep(pkg, 3)
    _booking(agent, pkg, dep)
    client.force_login(make_user("ops@example.com", R.OPERATIONS))
    for url in ("/trips/", "/trips/board/", "/trips/departures/", "/trips/departures/?stage=planned&export=csv",
                dep.get_absolute_url(), f"/trips/departures/{dep.pk}/manifest/", f"/trips/departures/{dep.pk}/manifest/?print=1",
                "/trips/departures/new/", f"/trips/departures/{dep.pk}/edit/", f"/trips/departures/{dep.pk}/incident/",
                "/trips/incidents/", "/trips/reports/"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert b"{{" not in r.content and b"{%" not in r.content, url
    client.post(reverse("trips:assign", args=[dep.pk]), {"guide": guide.pk, "driver": driver.pk, "vehicle": vehicle.pk})
    dep.refresh_from_db()
    assert dep.guide == guide


@pytest.mark.django_db
def test_guide_submits_trip_expense_pending_in_finance(client, make_user, pkg, crew):
    from apps.finance.models import Expense

    guide, driver, vehicle = crew
    u = make_user("guide@example.com", R.GUIDE)
    guide.user = u
    guide.save()
    dep = _dep(pkg, 1, guide=guide, vehicle=vehicle)
    client.force_login(u)
    assert client.get(reverse("trips:expense_new", args=[dep.pk])).status_code == 200
    r = client.post(reverse("trips:expense_new", args=[dep.pk]), {"category": "fuel", "amount": "4500", "method": "cash",
                                                                   "description": "Diesel at Narok"})
    assert r.status_code == 302
    e = Expense.objects.get()
    assert e.status == "pending" and e.departure == dep and e.submitted_by == u and not e.has_receipt and e.amount == Decimal("4500")
    other = _dep(pkg, 30)
    assert client.get(reverse("trips:expense_new", args=[other.pk])).status_code == 404


@pytest.mark.django_db
def test_fleet_fuel_and_maintenance_post_to_finance_once():
    from apps.finance import services as fin
    from apps.finance.models import Account, Expense
    from apps.fleet.models import FuelLog, MaintenanceLog

    v = Vehicle.objects.create(registration_no="KBB 111B")
    FuelLog.objects.create(vehicle=v, litres=Decimal("40"), amount=Decimal("5800"), station="Shell")
    m = MaintenanceLog.objects.create(vehicle=v, description="Service", cost=Decimal("23200"), garage="Toyota")
    m.description = "Edited"; m.save()                              # edits never double-post
    assert Expense.objects.filter(category="fuel", status="paid").count() == 1
    assert Expense.objects.filter(category="maintenance", status="paid").count() == 1
    b = fin._balances()
    assert fin._bal(Account.objects.get(key="exp_fuel"), b) == Decimal("5000.00")
    assert fin._bal(Account.objects.get(key="exp_maintenance"), b) == Decimal("20000.00")
    MaintenanceLog.objects.create(vehicle=v, description="Free check", cost=Decimal("0"))
    assert Expense.objects.count() == 2
