import pytest
from django.urls import reverse

from apps.accounts import roles as R
from apps.hr import services as hr
from apps.hr.models import AttendancePolicy, AttendanceRecord, OfficeLocation

pytestmark = pytest.mark.django_db
OFFICE = ("-1.286389", "36.817223")


@pytest.fixture
def office():
    return OfficeLocation.objects.create(name="Head office", latitude=OFFICE[0], longitude=OFFICE[1], radius_m=150)


def test_distance_and_geofence(make_user, office):
    assert hr.distance_m(*OFFICE, *OFFICE) == 0
    assert 1100 < hr.distance_m(*OFFICE, "-1.2964", "36.8172") < 1200          # ~1.15 km south
    u = make_user("a@example.com", R.SALES_AGENT)
    rec = hr.clock_in(u, "-1.28645", "36.81725", 12)
    assert rec.in_at_office and rec.in_distance_m < 20 and rec.in_office == office
    with pytest.raises(ValueError, match="already"):
        hr.clock_in(u, *OFFICE, 5)
    out = hr.clock_out(u, "-1.30", "36.82", 30)
    assert out.clock_out_at and not out.out_at_office and out.out_distance_m > 1000
    with pytest.raises(ValueError, match="not clocked in"):
        hr.clock_out(u)


def test_policy_can_require_office_and_location(make_user, office):
    u = make_user("b@example.com", R.SALES_AGENT)
    pol = AttendancePolicy.get(); pol.require_location = True; pol.save()
    with pytest.raises(ValueError, match="location"):
        hr.clock_in(u)
    pol.require_office = True; pol.save()
    with pytest.raises(ValueError, match="only clock in at the office"):
        hr.clock_in(u, "-1.35", "36.9", 10)
    assert hr.clock_in(u, *OFFICE, 10).in_at_office


def test_no_office_still_records_without_distance(make_user):
    rec = hr.clock_in(make_user("c@example.com", R.SALES_AGENT), "-1.3", "36.8", 20)
    assert rec.in_lat is not None and rec.in_distance_m is None and hr.describe(rec) == "No office set up"
    rec2 = hr.clock_in(make_user("d@example.com", R.SALES_AGENT))
    assert hr.describe(rec2) == "Location not recorded"


def test_endpoints_and_pages(client, make_user, office):
    u = make_user("e@example.com", R.SALES_AGENT)
    client.force_login(u)
    assert client.get(reverse("hr:clock_status")).json()["clocked_in"] is False
    r = client.post(reverse("hr:clock"), {"action": "in", "lat": OFFICE[0], "lng": OFFICE[1], "accuracy": "8"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["at_office"] and "map" in r.json()
    assert client.post(reverse("hr:clock"), {"action": "in"}).status_code == 400
    assert client.post(reverse("hr:clock"), {"action": "out", "lat": OFFICE[0], "lng": OFFICE[1]}).json()["ok"]
    assert client.get("/").status_code == 200 and b"data-clock" in client.get("/").content          # on the main menu
    assert client.get(reverse("hr:my_attendance")).status_code == 200
    assert client.get(reverse("hr:attendance")).status_code == 403                                    # HR view is for HR / executives
    admin = make_user("admin2@example.com", R.ADMIN)
    client.force_login(admin)
    page = client.get(reverse("hr:attendance"))
    assert page.status_code == 200 and b"e" in page.content and AttendanceRecord.objects.count() == 1
    assert client.get(reverse("hr:settings")).status_code == 200
    assert client.post(reverse("hr:settings"), {"action": "office", "name": "Branch", "latitude": "-4.05", "longitude": "39.66", "radius_m": "200"}).status_code == 302
    assert OfficeLocation.objects.filter(name="Branch").exists()


def test_sales_agent_main_menu_has_no_operations_sidebar(client, make_user):
    client.force_login(make_user("f@example.com", R.SALES_AGENT))
    html = client.get("/").content.decode()
    assert "Operations Dashboard" not in html and "Staff &amp; Guides" not in html
