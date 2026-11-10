"""Every lead lands at the Front Desk; only Front Desk, Sales Manager, COO and CEO distribute; Marketing tracks social leads only."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.leads import services as ls
from apps.leads.models import Enquiry, Stage
from apps.marketing import leadquality as lq
from apps.marketing import services as ms
from apps.people.models import Employee

pytestmark = pytest.mark.django_db


def _lead(source, name="Lead", phone="0711000001", **kw):
    return ls.create_enquiry(client_data={"full_name": name, "phone": phone}, enquiry_data={"source": source, **kw}, auto_assign=False)


@pytest.fixture
def agent_emp(make_user):
    u = make_user("ag@example.com", R.SALES_AGENT)
    return Employee.objects.create(user=u, full_name="Agent One", is_sales_agent=True)


@pytest.mark.parametrize("role,expected", [(R.FRONTDESK_MANAGER, True), (R.RESERVATIONS, True), (R.RECEPTIONIST, True), (R.SALES_MANAGER, True), (R.COO, True), (R.DIRECTOR, True),
                                           (R.SALES_AGENT, False), (R.MARKETING, False), (R.ADMIN, False), (R.FINANCE, False), (R.DISPATCHER, False)])
def test_who_can_distribute_leads(make_user, role, expected):
    assert make_user(f"{role.replace(' ', '')}@example.com", role).can_distribute_leads is expected


def test_assign_endpoints_enforce_it(client, make_user, agent_emp):
    e = _lead("whatsapp")
    for role in (R.MARKETING, R.SALES_AGENT, R.ADMIN):
        client.force_login(make_user(f"{role.replace(' ', '')}@example.com", role))
        assert client.post(reverse("leads:assign", args=[e.pk]), {"agent": agent_emp.pk}).status_code == 403
        assert client.post(reverse("frontdesk:assign_lead", args=[e.pk]), {"agent": agent_emp.pk}).status_code in (403, 404)
        assert client.post(reverse("sales:assignment_one", args=[e.pk])).status_code in (403, 404)
    client.force_login(make_user("desk@example.com", R.FRONTDESK_MANAGER))
    assert client.post(reverse("frontdesk:assign_lead", args=[e.pk]), {"agent": agent_emp.pk}).status_code == 302
    assert Enquiry.objects.get(pk=e.pk).assigned_agent == agent_emp


def test_social_and_website_leads_land_unassigned_at_front_desk(client, make_user, agent_emp):
    ms.ensure_channels()
    fb = ms.ingest_lead(name="Fb Lead", phone="0722000001", source="facebook")
    web = ms.ingest_lead(name="Web Lead", phone="0722000002", source="website")
    assert fb.assigned_agent_id is None and web.assigned_agent_id is None                    # no automatic hand-out by default
    client.force_login(make_user("desk2@example.com", R.FRONTDESK_MANAGER))
    html = client.get(reverse("frontdesk:queue"), {"period": "all"}).content.decode()
    assert "Fb Lead" in html and "Web Lead" in html
    assert "Fb Lead" in client.get(reverse("frontdesk:queue"), {"channel": "social", "period": "all"}).content.decode()
    assert "Web Lead" not in client.get(reverse("frontdesk:queue"), {"channel": "social", "period": "all"}).content.decode()
    assert "Web Lead" in client.get(reverse("frontdesk:queue"), {"channel": "other", "period": "all"}).content.decode()
    assert "Fb Lead" in client.get(reverse("frontdesk:dashboard")).content.decode()           # waiting to be assigned


def test_marketing_sees_only_social_leads_and_cannot_move_them(client, make_user):
    fb = _lead("facebook", "Social Sue", "0733000001")
    wa = _lead("whatsapp", "Walkin Wendy", "0733000002")
    mk = make_user("mk@example.com", R.MARKETING)
    client.force_login(mk)
    html = client.get("/marketing/enquiries/", {"range": "year"}).content.decode()
    assert "Social Sue" in html and "Walkin Wendy" not in html and "kmove" not in html and "hx-post" not in html
    assert client.get(reverse("marketing:lead_journey", args=[fb.pk])).status_code == 200
    assert client.get(reverse("marketing:lead_journey", args=[wa.pk])).status_code == 403
    page = client.get(reverse("marketing:lead_journey", args=[fb.pk])).content.decode()
    assert "0733000001" not in page and "Contact details stay with Sales" in page          # no phone / e-mail for marketing
    assert client.get(reverse("leads:detail", args=[fb.pk])).status_code in (302, 403)      # the shared lead screen is not for marketing
    from apps.core.modules import modules_for

    assert [m.key for m in modules_for(mk)][0] == "marketing" and "operations" not in [m.key for m in modules_for(mk)]


def test_lead_quality_funnel_and_lifetime(client, make_user, agent_emp):
    fb = _lead("facebook", "A", "0744000001")
    ig = _lead("instagram", "B", "0744000002")
    ig2 = _lead("instagram", "C", "0744000003")
    ls.assign_agent(ig, agent_emp)
    for e, st in ((ig, Stage.QUOTED), (ig, Stage.DEPOSIT), (ig2, Stage.QUOTED), (fb, Stage.QUOTED)):
        ls.change_stage(e, st.value)
    ls.change_stage(fb, Stage.LOST.value, lost_reason="Too expensive")            # lost, but it did reach "quoted"
    today = timezone.localdate()
    rows = {r["source"]: r for r in lq.quality_table(today - timedelta(days=1), today + timedelta(days=1))}
    assert rows["instagram"]["leads"] == 2 and rows["instagram"]["booked"] == 1 and rows["instagram"]["conversion"] == 50.0
    assert rows["facebook"]["steps"][1] == 1 and rows["facebook"]["lost"] == 1 and rows["facebook"]["booked"] == 0
    assert rows["tiktok"]["leads"] == 0
    life = lq.lifetime(ig)
    assert [s["stage"] for s in life["steps"]][-1] == "deposit" and life["reached"] == 4 and life["assigned_after_h"] is not None
    client.force_login(make_user("mk2@example.com", R.MARKETING))
    page = client.get(reverse("marketing:lead_quality"), {"range": "year"})
    assert page.status_code == 200 and b"Which platform sends good leads" in page.content and b"Instagram" in page.content
    assert client.get(reverse("marketing:lead_quality"), {"range": "year", "channel": "instagram", "stage": "deposit"}).status_code == 200
