from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts import roles as R
from apps.dealdesk import services as dd
from apps.dealdesk.models import BudgetRequest, ClientKYC, SignatureRequest
from apps.leads import services as lead_services
from apps.people.models import Employee
from apps.sales import services
from apps.sales.models import SalesSettings

pytestmark = pytest.mark.django_db


def _booking(agent):
    e = lead_services.create_enquiry(client_data={"full_name": "Wanjiru Kamau", "phone": "0712345678"}, enquiry_data={"source": "whatsapp", "adults": 2}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    q = services.create_quotation(e, agent.user, currency="KES", items=[{"kind": "other", "description": "Mara", "quantity": 1, "unit_price": 100000}])
    services.send_quotation(q, "whatsapp", agent.user)
    return services.accept_quotation(q, agent.user, deposit_percent=30), q


def test_kyc_gate_blocks_first_payment_only_when_enabled(agent, make_user):
    b, _ = _booking(agent)
    services.record_payment(b, agent.user, amount=1000)          # gate off by default
    b2, _ = _booking(agent)
    st = SalesSettings.get(); st.require_kyc_for_deposit = True; st.save()
    with pytest.raises(ValueError, match="KYC"):
        services.record_payment(b2, agent.user, amount=1000)
    kyc = dd.submit_kyc(b2.enquiry.client, id_type="national_id", id_number="12345678")
    mgr = make_user("m@example.com", R.SALES_MANAGER)
    dd.decide_kyc(kyc, mgr, True)
    services.record_payment(b2, agent.user, amount=1000)
    assert ClientKYC.objects.get(client=b2.enquiry.client).status == "verified"


def test_owner_cannot_verify_own_client(agent):
    b, _ = _booking(agent)
    c = b.enquiry.client
    c.owner = agent; c.save()
    kyc = dd.submit_kyc(c, id_type="passport", id_number="A1")
    with pytest.raises(PermissionError):
        dd.decide_kyc(kyc, agent.user, True)


def test_signature_flow_and_public_page(client, agent):
    b, q = _booking(agent)
    req = dd.request_signature(agent.user, quotation=q)
    assert client.get(f"/sign/{req.token}/").status_code == 200       # public, no login
    r = client.post(f"/sign/{req.token}/", {"name": "Wanjiru Kamau", "signature": "data:image/png;base64," + "A" * 300})
    req.refresh_from_db()
    assert r.status_code == 200 and req.status == SignatureRequest.SIGNED and req.signed_name == "Wanjiru Kamau"
    with pytest.raises(ValueError):
        dd.sign(req, name="x" * 5, image="data:image/png;base64," + "A" * 300, ip=None)


def test_signature_rejects_blank_and_expired(agent):
    b, q = _booking(agent)
    req = dd.request_signature(agent.user, booking=b)
    with pytest.raises(ValueError):
        dd.sign(req, name="Wanjiru", image="", ip=None)
    req.expires_at = timezone.now() - timedelta(days=1); req.save()
    with pytest.raises(ValueError, match="expired"):
        dd.sign(req, name="Wanjiru Kamau", image="data:image/png;base64," + "A" * 300, ip=None)


def test_payment_plan_splits_and_settles_oldest_first(agent):
    b, _ = _booking(agent)
    plan = dd.create_plan(b, agent.user, count=3, first_due=timezone.localdate() + timedelta(days=7))
    assert plan.total == b.balance and plan.instalments.count() == 3
    services.record_payment(b, agent.user, amount=Decimal("40000"))
    first = plan.instalments.first()
    assert first.paid_amount > 0 and plan.instalments.last().paid_amount == 0


def test_budget_request_rules(make_user):
    staff = make_user("s@example.com", R.SALES_AGENT)
    coo = make_user("coo@example.com", R.COO)
    br = dd.request_budget(staff, title="Trade fair", amount=Decimal("50000"))
    with pytest.raises(PermissionError):
        dd.decide_budget(br, staff, True)
    assert dd.decide_budget(br, coo, True).status == BudgetRequest.APPROVED


def test_missed_callback_task(agent):
    from apps.dealdesk.tasks import missed_callbacks
    from apps.sales.models import CallLog, FollowUp

    b, _ = _booking(agent)
    e = b.enquiry
    FollowUp.objects.filter(enquiry=e).delete()
    CallLog.objects.create(enquiry=e, agent=agent, outcome="busy", called_at=timezone.now() - timedelta(hours=3))
    missed_callbacks(); missed_callbacks()
    assert FollowUp.objects.filter(enquiry=e, title__startswith="Call back").count() == 1


def test_pages(client, agent, admin_user):
    b, q = _booking(agent)
    client.force_login(agent.user)
    for u in ("/deals/", "/deals/kyc/", f"/deals/kyc/client/{b.enquiry.client_id}/", "/deals/signatures/", f"/deals/plan/{b.pk}/", "/deals/budgets/", f"/sales/bookings/{b.pk}/", f"/sales/quotations/{q.pk}/"):
        assert client.get(u).status_code == 200, u
    assert client.post(f"/deals/signatures/request/booking/{b.pk}/").status_code == 302


def test_fixed_assets_depreciation_posts_once(admin_client):
    from datetime import date

    from apps.assets import services as asv
    from apps.assets.models import FixedAsset
    from apps.finance.models import JournalEntry

    a = FixedAsset.objects.create(name="Land Cruiser", category="vehicle", cost=Decimal("6000000"), residual_value=Decimal("600000"), useful_life_months=60, purchase_date=date(2026, 1, 15))
    assert a.tag.startswith("FA-") and a.monthly_depreciation == Decimal("90000.00")
    run = asv.post_depreciation("2026-03")
    assert run.amount == Decimal("90000.00")
    asv.post_depreciation("2026-03")
    assert JournalEntry.objects.filter(source_type="depreciation").count() == 1
    assert a.accumulated(date(2026, 4, 30)) == Decimal("270000.00")
    assert admin_client.get("/assets/").status_code == 200


def test_public_catalogue_and_enquiry(client):
    from apps.catalog.models import TourPackage
    from apps.leads.models import Enquiry

    p = TourPackage.objects.create(title="Mara Classic", days=3, nights=2, price_per_person=Decimal("90000"), status="published", summary="Big five")
    TourPackage.objects.create(title="Hidden Draft", status="draft")
    r = client.get("/explore/")
    assert r.status_code == 200 and b"Mara Classic" in r.content and b"Hidden Draft" not in r.content
    assert client.get("/explore/", {"q": "zzz"}).status_code == 200
    assert client.get("/explore/hidden-draft/").status_code == 404
    assert client.get(f"/explore/{p.slug}/").status_code == 200
    r = client.post(f"/explore/{p.slug}/", {"name": "Amina Said", "phone": "0712999888", "adults": 2, "message": "hi"})
    assert r.status_code == 200 and Enquiry.objects.filter(client__full_name="Amina Said", package=p).exists()
    client.post(f"/explore/{p.slug}/", {"name": "Bot", "phone": "0700000000", "adults": 2, "website": "spam.com"})
    assert not Enquiry.objects.filter(client__full_name="Bot").exists()
    assert b"/explore/" in client.get("/sitemap.xml").content and b"Sitemap" in client.get("/robots.txt").content


def test_sales_roles_reach_deal_tools_inside_sales(client, agent, make_user):
    from apps.core.modules import modules_for

    assert [m.key for m in modules_for(agent.user)] == ["sales", "me"]
    client.force_login(agent.user)
    for u in ("/deals/kyc/", "/deals/signatures/"):
        r = client.get(u)
        assert r.status_code == 200 and b"Deal Desk" not in r.content.split(b"<main")[0]     # sidebar is the Sales one
    assert client.get("/ict/chats/").status_code == 403                                      # chats are managers only
    mgr = make_user("m2@example.com", R.SALES_MANAGER)
    client.force_login(mgr)
    assert client.get("/ict/chats/").status_code == 200
    assert client.get("/ict/settings/").status_code == 403


def test_new_client_opens_as_a_popup_form(client, admin_user):
    from apps.contacts.models import Client

    client.force_login(admin_user)
    html = client.get("/clients/").content.decode()
    assert 'data-modal-form="/clients/new/"' in html
    frag = client.get("/clients/new/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert frag.status_code == 200 and b"<html" not in frag.content and b"data-modal-form-body" in frag.content
    bad = client.post("/clients/new/", {"full_name": ""}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert bad.status_code == 422 and b"data-modal-form-body" in bad.content
    ok = client.post("/clients/new/", {"full_name": "Popup Client", "phone": "0733111222", "nationality": "Kenyan", "preferred_language": "English"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert ok.status_code == 200 and ok.json()["ok"] and Client.objects.filter(full_name="Popup Client").exists()
    assert client.get("/clients/new/").status_code == 200            # the full page still works without JavaScript
