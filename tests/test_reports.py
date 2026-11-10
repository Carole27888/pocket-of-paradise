import pytest
from django.core import mail

from apps.accounts import roles as R
from apps.reports import registry
from apps.reports.models import ScheduledReport
from apps.reports.tasks import send_scheduled

pytestmark = pytest.mark.django_db


def test_report_access_follows_module_access(make_user):
    agent = make_user("a@example.com", R.SALES_AGENT)
    keys = {r.key for r in registry.available(agent)}
    assert "leads_by_source" in keys and "outstanding_invoices" not in keys and "fixed_assets" not in keys
    with pytest.raises(PermissionError):
        registry.run("outstanding_invoices", agent)
    with pytest.raises(PermissionError):
        registry.run("agent_performance", agent)                     # in sales module but managers only
    boss = make_user("b@example.com", R.DIRECTOR)
    assert set(registry.REPORTS) <= {r.key for r in registry.available(boss)}


def test_every_report_runs_and_exports(admin_client):
    for key in registry.REPORTS:
        r = admin_client.get(f"/reports/{key}/")
        assert r.status_code == 200, key
        assert admin_client.get(f"/reports/{key}/?format=xlsx").status_code == 200, key
        assert admin_client.get(f"/reports/{key}/?format=csv").status_code == 200, key
    assert admin_client.get("/reports/").status_code == 200


def test_scoped_agent_report_only_counts_own_leads(agent, other_agent, client):
    from apps.leads import services as ls

    for ag, phone in ((agent, "0711111111"), (other_agent, "0722222222")):
        e = ls.create_enquiry(client_data={"full_name": f"C{phone}", "phone": phone}, enquiry_data={"source": "call"}, auto_assign=False)
        ls.assign_agent(e, ag, None, "manual")
    _, cols, rows = registry.run("leads_by_source", agent.user)
    assert sum(r[1] for r in rows) == 1


def test_csv_formula_injection_is_defused(admin_user):
    from apps.reports import export

    resp = export.to_csv("x", ["a"], [["=HYPERLINK(\"http://evil\")"]])
    assert b"'=HYPERLINK" in resp.content


def test_schedule_sends_xlsx_and_rechecks_access(admin_client, admin_user, make_user):
    admin_client.post("/reports/leads_by_source/schedule/", {"frequency": "daily", "range_days": 7})
    assert ScheduledReport.objects.filter(user=admin_user).count() == 1
    assert send_scheduled() == "1 sent"
    assert len(mail.outbox) == 1 and mail.outbox[0].attachments[0][0].endswith(".xlsx")
    assert send_scheduled() == "0 sent"                              # once per day
    ex = make_user("x@example.com", R.FRONTDESK_MANAGER)
    ScheduledReport.objects.create(user=ex, report_key="outstanding_invoices", frequency="daily")
    send_scheduled()
    assert "Failed" in ScheduledReport.objects.get(user=ex).last_status
