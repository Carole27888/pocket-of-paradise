import pytest

from apps.catalog.models import Destination


@pytest.mark.django_db
def test_token_and_jwt_auth(client, make_user):
    make_user("api@example.com")
    Destination.objects.create(name="Samburu", slug="samburu")
    r = client.post("/api/v1/auth/token/", {"username": "api@example.com", "password": "pass12345"})
    assert r.status_code == 200, r.content
    token = r.json()["token"]
    r = client.get("/api/v1/destinations/", HTTP_AUTHORIZATION=f"Token {token}")
    assert r.status_code == 200 and b"Samburu" in r.content
    r = client.post("/api/v1/auth/jwt/", {"email": "api@example.com", "password": "pass12345"})
    assert r.status_code == 200 and "access" in r.json()


@pytest.mark.django_db
def test_api_requires_auth(client):
    assert client.get("/api/v1/enquiries/").status_code in (401, 403)


@pytest.mark.django_db
def test_api_departures_manifest_and_scoping(client, make_user):
    from datetime import timedelta
    from django.utils import timezone

    from apps.accounts import roles as R
    from apps.catalog.models import TourPackage
    from apps.people.models import Employee
    from apps.trips.models import Departure, ManifestEntry

    pkg = TourPackage.objects.create(title="Mara", days=2, nights=1, status="published")
    guide_user = make_user("g@example.com", R.GUIDE)
    guide = Employee.objects.create(full_name="G One", is_guide=True, user=guide_user)
    mine = Departure.objects.create(package=pkg, start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=1), guide=guide)
    other = Departure.objects.create(package=pkg, start_date=timezone.localdate() + timedelta(days=9), end_date=timezone.localdate() + timedelta(days=10))
    e = ManifestEntry.objects.create(departure=mine, full_name="Guest One", passport_number="A1234567", medical_notes="Asthma")
    client.force_login(guide_user)
    r = client.get("/api/v1/departures/")
    assert r.status_code == 200 and [d["reference"] for d in r.json()["results"]] == [mine.reference]
    assert client.get(f"/api/v1/departures/{other.pk}/").status_code == 404
    rows = client.get(f"/api/v1/departures/{mine.pk}/manifest/").json()
    assert rows[0]["passport"].startswith("••••") and "A1234567" not in str(rows) and rows[0]["medical_notes"] == "Asthma"
    r = client.post(f"/api/v1/departures/{mine.pk}/manifest/{e.pk}/check-in/", {}, content_type="application/json")
    assert r.status_code == 200 and r.json()["checked_in_at"]
    assert client.get("/api/v1/bookings/").status_code == 403           # guides have no Sales access
    assert client.get("/api/v1/guests/").status_code == 403
    assert client.get("/api/v1/movement/").status_code == 403


@pytest.mark.django_db
def test_api_agent_sees_only_own_bookings_and_guests(client, agent, other_agent):
    from apps.leads import services as lead_services
    from apps.sales import services as sales

    for ag, phone, name in ((agent, "0711000001", "Mine"), (other_agent, "0711000002", "Theirs")):
        e = lead_services.create_enquiry(client_data={"full_name": name, "phone": phone}, enquiry_data={"source": "call"}, auto_assign=False)
        lead_services.assign_agent(e, ag, None, "manual")
        q = sales.create_quotation(e, ag.user, currency="KES", items=[{"kind": "other", "description": "T", "quantity": 1, "unit_price": 1000}])
        sales.send_quotation(q, "email", ag.user)
        sales.accept_quotation(q, ag.user)
    client.force_login(agent.user)
    names = [b["client_name"] for b in client.get("/api/v1/bookings/").json()["results"]]
    assert names == ["Mine"]
    assert [g["full_name"] for g in client.get("/api/v1/guests/").json()["results"]] == ["Mine"]
    assert "passport" not in client.get("/api/v1/guests/").content.decode()
