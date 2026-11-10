import pytest
from django.urls import reverse

from apps.accounts import roles as R
from apps.catalog.models import Destination
from apps.contacts.models import Client
from apps.leads import services
from apps.leads.models import Enquiry, LeadActivity, Stage
from apps.notifications.models import Notification


def _enquiry(phone="0712345678", **kw):
    return services.create_enquiry(client_data={"full_name": kw.pop("name", "Wanjiru Kamau"), "phone": phone,
                                                "email": kw.pop("email", "")},
                                   enquiry_data={"source": "whatsapp", **kw})


@pytest.mark.django_db
def test_returning_client_detected_by_phone_in_any_format():
    first = _enquiry("0712 345 678")
    second = _enquiry("+254712345678", name="W. Kamau")
    assert first.client_id == second.client_id
    assert not first.is_returning_client and second.is_returning_client
    assert Client.objects.count() == 1


@pytest.mark.django_db
def test_returning_client_detected_by_email():
    _enquiry("0711111111", email="guest@example.com")
    e = _enquiry("0722222222", email="Guest@Example.com")
    assert e.is_returning_client


@pytest.mark.django_db
def test_returning_client_by_passport():
    c = Client.objects.create(full_name="Lukas Schneider", phone="+491512345000", passport_number="C01X 00T47")
    m = services.match_client(passport="c01x00t47")
    assert m.returning and m.client == c


@pytest.mark.django_db
def test_enquiry_reference_and_created_activity():
    e = _enquiry()
    assert e.reference == "ENQ-00001"
    assert e.activities.filter(activity_type="created").exists()


@pytest.mark.django_db
def test_stage_change_logs_and_requires_lost_reason():
    e = _enquiry()
    services.change_stage(e, Stage.QUOTED)
    assert e.activities.filter(activity_type="stage").exists()
    with pytest.raises(ValueError):
        services.change_stage(e, Stage.LOST)
    services.change_stage(e, Stage.LOST, lost_reason="Price")
    assert Enquiry.objects.get(pk=e.pk).lost_reason == "Price"


@pytest.mark.django_db(transaction=True)
def test_assignment_notifies_agent_and_refuses_off_duty(agent, other_agent):
    e = _enquiry()
    services.assign_agent(e, agent)
    assert Notification.objects.filter(user=agent.user, title__contains=e.reference).exists()
    other_agent.on_duty = "on_leave"
    other_agent.save()
    with pytest.raises(ValueError):
        services.assign_agent(e, other_agent)
    services.assign_agent(e, agent)  # idempotent
    assert e.assignments.filter(active=True).count() == 1


@pytest.mark.django_db
def test_agent_sees_only_own_enquiries(client, agent, other_agent):
    mine = _enquiry("0711000001")
    theirs = _enquiry("0711000002")
    services.assign_agent(mine, agent)
    services.assign_agent(theirs, other_agent)
    client.force_login(agent.user)
    listing = client.get(reverse("leads:list")).content.decode()
    assert mine.reference in listing and theirs.reference not in listing
    assert client.get(reverse("leads:detail", args=[mine.pk])).status_code == 200
    assert client.get(reverse("leads:detail", args=[theirs.pk])).status_code == 403
    search = client.get(reverse("core:search"), {"q": "ENQ"}).content.decode()
    assert theirs.reference not in search


@pytest.mark.django_db
def test_manager_sees_all(client, make_user, agent, other_agent):
    a, b = _enquiry("0711000001"), _enquiry("0711000002")
    services.assign_agent(a, agent)
    services.assign_agent(b, other_agent)
    mgr = make_user("mgr@example.com", R.SALES_MANAGER)
    client.force_login(mgr)
    html = client.get(reverse("leads:list")).content.decode()
    assert a.reference in html and b.reference in html


@pytest.mark.django_db
def test_create_enquiry_via_form(client, make_user, agent):
    admin_client = client
    client.force_login(make_user("desk@example.com", R.FRONTDESK_MANAGER))
    d = Destination.objects.create(name="Maasai Mara", slug="maasai-mara")
    r = admin_client.post(reverse("leads:create"), {
        "full_name": "Achieng Otieno", "phone": "0722 000 111", "email": "", "nationality": "Kenyan",
        "source": "walk_in", "purpose": "new", "destination": d.pk, "adults": 2, "children": 0,
        "assigned_agent": agent.pk, "notes": "Wants the migration"})
    assert r.status_code == 302
    e = Enquiry.objects.get()
    assert e.client.phone == "+254722000111" and e.assigned_agent == agent and e.source == "walk_in"
    assert e.captured_by is not None


@pytest.mark.django_db
def test_returning_banner_partial(admin_client):
    _enquiry("0712345678")
    r = admin_client.get(reverse("leads:returning_check"), {"phone": "712 345 678"})
    assert b"Returning client" in r.content


@pytest.mark.django_db
def test_htmx_stage_note_and_assign(admin_client, agent):
    e = _enquiry()
    from apps.sales.models import Quotation
    Quotation.objects.create(enquiry=e, title="T", currency="KES", fx_rate=1)   # Hot needs a quotation on record
    r = admin_client.post(reverse("leads:stage", args=[e.pk]), {"stage": "hot"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    assert Enquiry.objects.get(pk=e.pk).stage == "hot"
    r = admin_client.post(reverse("leads:note", args=[e.pk]), {"activity_type": "call", "details": "Called"},
                          HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "toast" in r["HX-Trigger"]
    assert Enquiry.objects.get(pk=e.pk).last_contact_at is not None
    assert admin_client.post(reverse("leads:assign", args=[e.pk]), {"agent": agent.pk}, HTTP_HX_REQUEST="true").status_code == 403   # Admin doesn't distribute leads
    from apps.accounts.models import User
    admin_client.force_login(User.objects.create_user("fd@example.com", "pass12345", full_name="Desk"))
    from django.contrib.auth.models import Group
    User.objects.get(email="fd@example.com").groups.add(Group.objects.get_or_create(name=R.FRONTDESK_MANAGER)[0])
    r = admin_client.post(reverse("leads:assign", args=[e.pk]), {"agent": agent.pk}, HTTP_HX_REQUEST="true")
    assert r.status_code == 204
    assert Enquiry.objects.get(pk=e.pk).assigned_agent == agent
    assert LeadActivity.objects.filter(enquiry=e, activity_type="assignment").exists()


@pytest.mark.django_db
def test_list_exports(admin_client):
    _enquiry()
    csv = admin_client.get(reverse("leads:list"), {"export": "csv"})
    assert csv["Content-Type"] == "text/csv" and b"ENQ-00001" in csv.content
    xlsx = admin_client.get(reverse("leads:list"), {"export": "xlsx"})
    assert xlsx.content[:2] == b"PK"


@pytest.mark.django_db
def test_bulk_stage_moves_only_visible_enquiries(client, agent, other_agent, make_user):
    mine, theirs = _enquiry("0711000001"), _enquiry("0711000002", name="Other")
    services.assign_agent(mine, agent)
    services.assign_agent(theirs, other_agent)
    from apps.sales.models import Quotation
    for e in (mine, theirs):
        Quotation.objects.create(enquiry=e, title="T", currency="KES", fx_rate=1)   # Hot needs a quotation on record
    client.force_login(agent.user)
    r = client.post(reverse("leads:bulk_stage"), {"ids": [mine.pk, theirs.pk], "stage": "hot"})
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    mine.refresh_from_db(); theirs.refresh_from_db()
    assert mine.stage == Stage.HOT and theirs.stage == Stage.ENQUIRY
    assert mine.activities.filter(activity_type="stage").exists()
    # lost needs a reason; nothing changes without one
    client.post(reverse("leads:bulk_stage"), {"ids": [mine.pk], "stage": "lost"})
    mine.refresh_from_db(); assert mine.stage == Stage.HOT
    client.post(reverse("leads:bulk_stage"), {"ids": [mine.pk], "stage": "lost", "lost_reason": "Price"})
    mine.refresh_from_db(); assert mine.stage == Stage.LOST and mine.lost_reason == "Price"
    director = make_user("dir@example.com", R.DIRECTOR)
    client.force_login(director)
    assert client.post(reverse("leads:bulk_stage"), {"ids": [mine.pk], "stage": "hot"}).status_code == 403
