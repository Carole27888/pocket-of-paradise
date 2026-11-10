"""Every Phase 1 screen renders for the roles that can open it, and is blocked for the rest."""
import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Accommodation, Destination, TourPackage
from apps.contacts.models import Client
from apps.fleet.models import Vehicle
from apps.leads.models import Enquiry
from apps.people.models import Employee


@pytest.fixture(scope="module")
def seeded(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        call_command("seed_demo", "--reset", verbosity=0)
        yield
        call_command("flush", "--no-input", verbosity=0)


def _urls():
    e = Enquiry.objects.filter(assigned_agent__isnull=False).first()
    d, p, a = Destination.objects.first(), TourPackage.objects.first(), Accommodation.objects.first()
    c, v, s = Client.objects.first(), Vehicle.objects.first(), Employee.objects.first()
    return ["/", "/operations/", "/enquiries/", "/enquiries/?stage=hot", "/enquiries/new/", e.get_absolute_url(),
            f"/enquiries/{e.pk}/edit/", "/clients/", c.get_absolute_url(), "/clients/new/",
            "/catalog/destinations/", d.get_absolute_url(), "/catalog/packages/", p.get_absolute_url(),
            f"/catalog/packages/{p.slug}/itinerary/", "/catalog/accommodations/", a.get_absolute_url(),
            f"/catalog/accommodations/{a.pk}/rates/", "/fleet/", v.get_absolute_url(), "/fleet/new/", "/staff/",
            s.get_absolute_url(), "/staff/new/", "/search/?q=mara", "/system/status/", "/accounts/users/",
            "/accounts/users/new/", "/system/company/", "/accounts/install/", "/notifications/"] + _phase_urls()


def _phase_urls():
    """Trips, Finance, Suppliers and Marketing screens built from seeded records."""
    from apps.finance.models import Expense, Invoice, PayrollRun, Receipt
    from apps.finance import reports
    from apps.marketing.models import Campaign, Post, TrackedLink
    from apps.suppliers.models import Bill, Supplier
    from apps.trips.models import Departure, Incident

    d = Departure.objects.filter(stage="completed").first()
    inv, rc, ex = Invoice.objects.first(), Receipt.objects.first(), Expense.objects.first()
    run, bill, sup = PayrollRun.objects.first(), Bill.objects.first(), Supplier.objects.first()
    camp, post, link = Campaign.objects.first(), Post.objects.first(), TrackedLink.objects.first()
    inc = Incident.objects.first()
    urls = ["/trips/", "/trips/board/", "/trips/departures/", d.get_absolute_url(), f"/trips/departures/{d.pk}/manifest/",
            "/trips/incidents/", "/trips/reports/", "/sales/double-allocation/",
            "/finance/", "/finance/revenue/", "/finance/receivables/", "/finance/invoices/", inv.get_absolute_url(),
            "/finance/receipts/", rc.get_absolute_url(), "/finance/payables/", "/finance/expenses/", ex.get_absolute_url(),
            "/finance/kra/", "/finance/tax-settings/", "/finance/payroll/", run.get_absolute_url(), "/finance/calendar/",
            "/finance/statements/", "/finance/statements/?type=balance", "/finance/statements/?type=cash", "/finance/budgets/",
            "/finance/reports/", "/suppliers/", sup.get_absolute_url(), "/suppliers/bills/", bill.get_absolute_url(),
            "/suppliers/departures/", "/marketing/", "/marketing/social/", "/marketing/channels/facebook/", "/marketing/inbox/",
            "/marketing/enquiries/", "/marketing/campaigns/", camp.get_absolute_url(), "/marketing/content/",
            "/marketing/posts/", post.get_absolute_url(), "/marketing/media/", "/marketing/reviews/", "/marketing/analytics/",
            "/marketing/accounts/", "/marketing/links/", link.get_absolute_url(), "/marketing/assignment/",
            "/marketing/automation/", "/marketing/integrations/"]
    urls += [f"/finance/reports/{s}/" for s in reports.REPORTS]
    if inc:
        urls.append(inc.get_absolute_url())
    return urls


@pytest.mark.django_db
def test_admin_can_open_every_screen(client, seeded):
    client.force_login(User.objects.get(email="admin@pocketofparadise.co.ke"))
    for url in _urls():
        r = client.get(url)
        assert r.status_code == 200, url
        assert b"{{" not in r.content and b"{%" not in r.content, url


@pytest.mark.django_db
@pytest.mark.parametrize("email", ["director", "sarah", "salesmanager", "frontdesk", "deskmanager", "reception", "dispatch",
                                   "operations", "finance", "production", "marketing", "suppliers", "john"])
def test_other_roles_never_error(client, seeded, email):
    client.force_login(User.objects.get(email=f"{email}@pocketofparadise.co.ke"))
    for url in _urls():
        r = client.get(url)
        assert r.status_code in (200, 302, 403, 404), (url, r.status_code)


@pytest.mark.django_db
def test_admin_pages_blocked_for_non_admin(client, seeded):
    client.force_login(User.objects.get(email="sarah@pocketofparadise.co.ke"))
    for url in ("/system/status/", "/accounts/users/", "/system/company/", "/staff/new/", "/fleet/new/"):
        assert client.get(url).status_code == 403, url


@pytest.mark.django_db
def test_htmx_list_returns_partial(client, seeded):
    client.force_login(User.objects.get(email="admin@pocketofparadise.co.ke"))
    r = client.get("/enquiries/", {"q": "a"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"<html" not in r.content and b"<table" in r.content


@pytest.mark.django_db
def test_seed_matches_spec_pipeline(seeded):
    counts = {s: Enquiry.objects.filter(stage=s).count() for s in
              ("enquiry", "quoted", "hot", "itinerary", "deposit", "confirmed", "completed", "lost")}
    assert counts == {"enquiry": 86, "quoted": 64, "hot": 38, "itinerary": 29, "deposit": 14, "confirmed": 9,
                      "completed": 16, "lost": 21}
    assert Vehicle.objects.count() == 8


def test_login_required(client, db):
    r = client.get("/operations/")
    assert r.status_code == 302 and "/accounts/login/" in r["Location"]


@pytest.mark.django_db
def test_login_rate_limit(client, make_user):
    make_user("x@example.com")
    for _ in range(5):
        client.post("/accounts/login/", {"email": "x@example.com", "password": "wrong"})
    r = client.post("/accounts/login/", {"email": "x@example.com", "password": "pass12345"})
    assert b"Too many attempts" in r.content


@pytest.mark.django_db
def test_login_and_logout(client, make_user):
    make_user("y@example.com")
    r = client.post("/accounts/login/", {"email": "Y@example.com", "password": "pass12345", "remember": "on"})
    assert r.status_code == 302
    assert client.post("/accounts/logout/").status_code == 302
    assert client.get("/operations/").status_code == 302


@pytest.mark.django_db
def test_fleet_and_duty_updates(admin_client, make_user):
    v = Vehicle.objects.create(registration_no="KDA 001A", odometer_km=1000)
    admin_client.post(reverse("fleet:set_status", args=[v.pk]), {"status": "maintenance"})
    admin_client.post(reverse("fleet:add_fuel", args=[v.pk]), {"date": "2026-09-01", "litres": "50", "amount": "9000",
                                                              "currency": "KES", "odometer_km": "1500"})
    v.refresh_from_db()
    assert v.status == "maintenance" and v.odometer_km == 1500 and v.fuel_logs.count() == 1
    e = Employee.objects.create(full_name="John Lekishon", is_guide=True)
    r = admin_client.post(reverse("people:set_duty", args=[e.pk]), {"on_duty": "out_on_trip"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    e.refresh_from_db()
    assert e.on_duty == "out_on_trip"


@pytest.mark.django_db
def test_package_itinerary_formset(admin_client):
    d = Destination.objects.create(name="Amboseli Test", slug="amboseli-test")
    p = TourPackage.objects.create(title="Amboseli 2 days", days=2, nights=1)
    p.destinations.add(d)
    url = reverse("catalog:package_itinerary", args=[p.slug])
    assert admin_client.get(url).status_code == 200
    data = {"days-TOTAL_FORMS": "2", "days-INITIAL_FORMS": "0", "days-MIN_NUM_FORMS": "0", "days-MAX_NUM_FORMS": "1000",
            "days-0-day_number": "1", "days-0-title": "Nairobi to Amboseli", "days-0-destination": d.pk,
            "days-0-breakfast": "", "days-0-lunch": "on", "days-0-dinner": "on",
            "days-1-day_number": "2", "days-1-title": "Return", "days-1-breakfast": "on"}
    r = admin_client.post(url, data)
    assert r.status_code == 302, r.content[:500]
    p.refresh_from_db()
    assert [x.meals_code for x in p.itinerary.days.all()] == ["LD", "B"]
    assert p.code.startswith("PKG-")


@pytest.mark.django_db
def test_seed_can_be_reset_repeatedly():
    """seed_demo --reset must wipe every table the seed fills (a second run once failed on a unique constraint)."""
    call_command("seed_demo", "--reset", verbosity=0)
    call_command("seed_demo", "--reset", verbosity=0)
    from apps.frontdesk.models import EquipmentRecord
    assert EquipmentRecord.objects.count() == 24
