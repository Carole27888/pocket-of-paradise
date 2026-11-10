import json
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.ict import ai, backups
from apps.leads import services as ls
from apps.mailbox.models import Mailbox, MailSettings, SentEmail
from apps.notifications.models import Notification
from apps.people.models import Employee, Specialty
from apps.sales import services as ss
from apps.system.services import save_credentials
from apps.tasks import services as ts
from apps.tasks.models import Task

pytestmark = pytest.mark.django_db


def _enq(name="Amani", phone="0711000111", **kw):
    return ls.create_enquiry(client_data={"full_name": name, "phone": phone, "email": "amani@example.com"}, enquiry_data={"source": "whatsapp", **kw}, auto_assign=False)


# ---------------------------------------------------------------- specialist routing
def test_specialist_gets_the_lead_over_round_robin(agent, other_agent):
    mara = Specialty.objects.get_or_create(name="Maasai Mara", slug="maasai-mara")[0]
    coast = Specialty.objects.get_or_create(name="Coastal beach", slug="coastal-beach")[0]
    other = other_agent
    agent.specialties.set([coast]); other.specialties.set([mara])
    e = _enq(destination_text="Maasai Mara 4 days")
    chosen, method, why = ss.pick_agent(e)
    assert chosen == other and "Specialist" in why


# ---------------------------------------------------------------- tasks
def test_overdue_task_escalates_to_manager_then_executives(agent, make_user):
    boss = Employee.objects.create(user=make_user("boss@example.com", R.SALES_MANAGER, full_name="Boss"), full_name="Boss")
    make_user("coo2@example.com", R.COO, full_name="Coo")
    agent.manager = boss
    agent.save()
    t = ts.create(title="Send invoice", owner=agent, user=boss.user, due_at=timezone.now() - timedelta(hours=3))
    assert ts.escalate_overdue() == 1
    t.refresh_from_db()
    assert t.escalation_level == 1 and Notification.objects.filter(user=boss.user, title__startswith="Overdue").exists()
    assert ts.escalate_overdue() == 0  # not again within a day
    Task.objects.filter(pk=t.pk).update(escalated_at=timezone.now() - timedelta(hours=25))
    assert ts.escalate_overdue() == 1
    t.refresh_from_db()
    assert t.escalation_level == 2 and Notification.objects.filter(user__email="coo2@example.com", title__startswith="Overdue").exists()


def test_complaint_from_enquiry_is_logged_and_listed(client, agent):
    e = _enq()
    ls.assign_agent(e, agent, None, "manual")
    client.force_login(agent.user)
    r = client.post(reverse("tasks:new") + f"?kind=complaint&enquiry={e.pk}", {"title": "Late reply", "owner": agent.pk, "due_at": (timezone.localtime() + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M"), "priority": "high", "details": "x"},
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert r.status_code == 200 and json.loads(r.content)["ok"]
    assert Task.objects.get().kind == "complaint" and e.activities.filter(details__startswith="Complaint logged").exists()
    assert b"Late reply" in client.get(reverse("tasks:list") + "?view=complaints").content


# ---------------------------------------------------------------- mailbox
def test_client_email_is_sent_with_director_cc_and_logged(client, agent):
    e = _enq()
    ls.assign_agent(e, agent, None, "manual")
    MailSettings.objects.update_or_create(pk=1, defaults={"director_cc": "ceo@example.com", "cc_enabled": True})
    Mailbox.objects.create(user=agent.user, email="sarah@example.com", display_name="Sarah", smtp_host="smtp.example.com", password="pw")
    client.force_login(agent.user)
    assert client.get(reverse("mailbox:compose") + f"?enquiry={e.pk}", HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code == 200
    r = client.post(reverse("mailbox:compose"), {"enquiry": e.pk, "to": "amani@example.com", "subject": "Your safari", "body": "Hello"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert json.loads(r.content)["ok"]
    assert mail.outbox[-1].cc == ["ceo@example.com"] and SentEmail.objects.get().status == "sent"
    assert e.activities.filter(activity_type="email").exists()


def test_mailbox_password_is_encrypted_at_rest(agent):
    from django.db import connection

    Mailbox.objects.create(user=agent.user, email="a@example.com", display_name="A", smtp_host="h", password="secret-pw")
    with connection.cursor() as c:
        c.execute("select password from mailbox_mailbox")
        assert c.fetchone()[0].startswith("enc$")


# ---------------------------------------------------------------- backup
def test_backup_excludes_secrets_and_first_download_closes_it(client, admin_user):
    save_credentials("sms", {"api_key": "SUPERSECRET", "username": "sandbox"})
    run = backups.create_backup()
    data = run.file.read().decode()
    assert "SUPERSECRET" not in data and run.object_count > 0
    assert not backups.is_due()
    admin = admin_user
    client.force_login(admin)
    r = client.get(reverse("ict:backup_download", args=[run.pk]))
    assert r.status_code == 200
    run.refresh_from_db()
    assert run.downloaded_by == admin


# ---------------------------------------------------------------- AI provider choice
def test_openai_is_used_when_preferred_or_when_claude_is_missing(monkeypatch):
    save_credentials("openai", {"api_key": "sk-test"})
    assert ai.configured() and ai.active_provider() == "openai"
    seen = {}

    def fake_http(url, headers, payload):
        seen["url"] = url
        return {"choices": [{"message": {"content": "Habari!"}}]}

    monkeypatch.setattr(ai, "_http", fake_http)
    resp = ai.complete("be brief", [{"role": "user", "content": "hi"}])
    assert ai.text_of(resp) == "Habari!" and "openai.com" in seen["url"]


def test_falls_back_to_the_other_provider(monkeypatch):
    save_credentials("ai", {"api_key": "sk-ant"})
    save_credentials("openai", {"api_key": "sk-test"})
    calls = []

    def fake_http(url, headers, payload):
        calls.append(url)
        if "anthropic" in url:
            raise ai.AIUnavailable("down")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai, "_http", fake_http)
    assert ai.text_of(ai.complete("s", [{"role": "user", "content": "x"}])) == "ok" and len(calls) == 2


# ---------------------------------------------------------------- insights pages
def test_sales_and_research_pages_render_for_the_manager(client, make_user):
    u = make_user("coo@example.com", R.COO, full_name="Coo")
    _enq(destination_text="Amboseli")
    client.force_login(u)
    for name in ("insights:sales", "insights:research", "tasks:list", "mailbox:inbox", "mailbox:setup", "mailbox:settings", "ict:backups"):
        assert client.get(reverse(name)).status_code == 200, name


# ---------------------------------------------------------------- Assign Leads page
def test_assign_leads_page_filters_and_bulk_assigns(client, make_user, agent):
    mgr = make_user("sm@example.com", R.SALES_MANAGER, full_name="Sales Mgr")
    e1, e2 = _enq("One", "0711000201"), _enq("Two", "0711000202", destination_text="Amboseli")
    client.force_login(mgr)
    r = client.get(reverse("sales:assign_leads"))
    assert r.status_code == 200 and b"One" in r.content and b"Agent capacity today" in r.content
    r = client.post(reverse("sales:assign_leads_bulk"), {"ids": [e1.pk, e2.pk], "agent": agent.pk})
    assert r.status_code == 302
    e1.refresh_from_db(); e2.refresh_from_db()
    assert e1.assigned_agent == agent and e2.assigned_agent == agent
    assert b"One" not in client.get(reverse("sales:assign_leads")).content          # no longer waiting
    client.force_login(agent.user)
    assert client.get(reverse("sales:assign_leads")).status_code == 403              # agents cannot distribute


# ---------------------------------------------------------------- ICT: dashboard, keys, work log, imports in ICT
def test_ict_pages_render_and_bot_toggle_flips(admin_client):
    from apps.ict.models import BotSettings

    for name in ("ict:dashboard", "ict:chats", "ict:analysis", "ict:settings", "ict:imports", "ict:work_log", "ict:calendar", "ict:backups"):
        r = admin_client.get(reverse(name))
        assert r.status_code == 200, name
        assert b"Back to main menu" not in r.content          # one sidebar for every module
    before = BotSettings.get().enabled
    admin_client.post(reverse("ict:bot_toggle"), {"next": "/ict/"})
    assert BotSettings.get().enabled is (not before)


def test_ict_keys_saved_from_ict_stay_in_ict_and_ai_choice_applies(admin_client):
    from apps.ict.models import BotSettings
    from apps.system.services import get_credential

    r = admin_client.post(reverse("ict:credential_save", args=["openai"]), {"api_key": "sk-test-1234567890"})
    assert r.status_code == 302 and r.url == reverse("ict:settings")
    admin_client.post(reverse("ict:ai_choice"), {"prefer": "openai"})
    assert get_credential("ai", "prefer") == "openai"
    assert BotSettings.get().ai_fallback is False             # box left unticked


def test_ict_work_log_and_fallback_message(admin_client, admin_user):
    from apps.ict.models import BotSettings, WorkLogEntry
    from apps.people.models import Employee

    Employee.objects.get_or_create(user=admin_user, defaults={"full_name": "Admin", "title": "ICT"})
    admin_client.post(reverse("ict:work_log"), {"description": "Replaced the router", "date": "2026-11-07"})
    assert WorkLogEntry.objects.filter(description="Replaced the router").exists()
    admin_client.post(reverse("ict:settings"), {"fallback_message": "We'll be right back", "bot_name": "Zuri", "max_bot_replies": "9"})
    assert BotSettings.get().fallback_message == "We'll be right back"


def test_import_screens_work_under_ict_and_admin(admin_client):
    r = admin_client.get(reverse("ict:imports"))
    assert b"/ict/import/template/" in r.content
    r2 = admin_client.get(reverse("system:imports"))
    assert b"/system/import/template/" in r2.content


# ---------------------------------------------------------------- Intelligence module, per-module manual/calendar, production, notifications
def test_new_screens_render_and_stay_in_their_module(admin_client):
    for name, module in [("insights:centre", "Intelligence"), ("insights:sales", "Intelligence"), ("insights:research", "Intelligence"),
                         ("production:overview", "Trip Production"), ("production:reports", "Trip Production"),
                         ("notifications:list", "My Space"), ("manual_sales", "Sales"), ("calendar_sales", "Sales"),
                         ("manual_finance", "Finance"), ("calendar_marketing", "Marketing"), ("manual_ict", "ICT")]:
        r = admin_client.get(reverse(name))
        assert r.status_code == 200, name
        assert r.context["current_module"].name.startswith(module[:6]), (name, r.context["current_module"].name)


def test_bank_statement_imports_matches_and_reconciles(admin_client):
    from datetime import date
    from decimal import Decimal

    from apps.finance import bank, services
    from apps.finance.models import Account, BankStatementLine

    services.ensure_chart()
    today = date(2026, 11, 3)
    services.post(today, "Deposit from Amani", [("bank", 5000, 0), ("deposits", 0, 5000)] if False else [("bank", 5000, 0), ("cash", 0, 5000)])
    acct = Account.objects.get(key="bank")
    csv_bytes = b"Date,Narration,Reference,Debit,Credit,Balance\n2026-11-03,Deposit Amani,TX1,,5000,5000\n2026-11-04,Bank charges,TX2,150,,4850\n"
    r = bank.import_statement(acct, "stmt.csv", csv_bytes)
    assert r["created"] == 2 and r["matched"] == 1
    assert bank.import_statement(acct, "stmt.csv", csv_bytes)["duplicates"] == 2          # same file twice adds nothing
    charge = BankStatementLine.objects.get(reference="TX2")
    assert charge.status == "unmatched" and charge.amount == Decimal("-150")
    resp = admin_client.get(reverse("finance:bank") + "?account=" + acct.code)
    assert resp.status_code == 200 and b"Bank charges" in resp.content
    admin_client.post(reverse("finance:bank_line_action", args=[charge.pk]), {"action": "ignore", "reason": "Charges, booked monthly", "account": acct.code})
    charge.refresh_from_db()
    assert charge.status == "ignored"
    rec = bank.reconcile(acct, date(2026, 11, 4), Decimal("5000"))
    assert rec.balanced


def test_commission_page_and_export(admin_client, agent):
    r = admin_client.get(reverse("finance:commission"))
    assert r.status_code == 200 and b"Sarah Njeri" in r.content
    r = admin_client.get(reverse("finance:commission") + "?export=csv")
    assert r["Content-Type"].startswith("text/csv") and b"Sarah Njeri" in r.content


def test_blog_reader_saves_posts_and_page_renders(admin_client, monkeypatch):
    from apps.marketing import blogs
    from apps.marketing.models import BlogPost

    feed = ('<?xml version="1.0"?><rss version="2.0"><channel><item><title>Maasai Mara in July</title><link>https://pocketofparadise.co.ke/blog/mara-july</link>'
            '<description>Migration season</description><pubDate>Tue, 03 Nov 2026 08:00:00 +0000</pubDate></item></channel></rss>')
    monkeypatch.setattr(blogs, "fetch", lambda url: feed)
    admin_client.post(reverse("marketing:blogs"), {"url": "https://pocketofparadise.co.ke/blog/", "is_active": "on"})
    r = admin_client.post(reverse("marketing:blogs_fetch"))
    assert r.status_code == 302 and BlogPost.objects.filter(title="Maasai Mara in July").exists()
    assert b"Maasai Mara in July" in admin_client.get(reverse("marketing:blogs")).content


def test_marketing_agents_page_renders(admin_client):
    assert admin_client.get(reverse("marketing:agents")).status_code == 200


def test_every_manual_screenshot_path_and_pwa_shortcut_resolves(admin_client):
    """A manual screenshot taken from a dead address captures a 404 page (it happened once): every address must exist."""
    import re
    from pathlib import Path

    src = "".join(Path(f"apps/manuals/{n}.py").read_text() for n in ("rich", "content"))
    paths = set(re.findall(r'shot_path="([^"]+)"', src))
    paths |= {s["url"].split("?")[0] for s in admin_client.get("/manifest.webmanifest").json()["shortcuts"]}
    assert len(paths) > 20
    for p in sorted(paths):
        assert admin_client.get(p.split("?")[0]).status_code in (200, 302, 403), p     # 403 = a screen for another role, still a real address


def test_channel_pages_render_every_tab_for_every_channel(admin_client):
    from apps.marketing import services as ms

    ms.ensure_channels()
    for key in ("facebook", "instagram", "tiktok", "google", "youtube"):
        for tab in ("overview", "posts", "ads", "inbox", "audience"):
            r = admin_client.get(reverse("marketing:channel", args=[key]) + f"?tab={tab}")
            assert r.status_code == 200, (key, tab)


# ---------------------------------------------------------------- ICT switch for automatic lead assignment
def test_routing_switch_stops_and_starts_automatic_assignment(admin_client, admin_user, agent, client, make_user):
    from apps.marketing import services as mkt
    from apps.sales.models import SalesSettings

    cfg = SalesSettings.get()
    cfg.auto_assign_new = True
    cfg.save()
    on = _enq("Wanjiru", "0711222333")
    ss.maybe_auto_assign(on)                                                   # what lead creation runs once the lead is saved
    on.refresh_from_db()
    assert on.assigned_agent_id == agent.pk                                    # on: the system assigns

    r = admin_client.post(reverse("ict:routing_toggle"), {"next": "/ict/"})
    assert r.status_code == 302 and SalesSettings.get().auto_assign_new is False
    off = _enq("Baraka", "0711444555")
    ss.maybe_auto_assign(off)
    off.refresh_from_db()
    assert off.assigned_agent_id is None                                       # off: the lead waits for a manager
    assert mkt.reassign_unresponsive() == 0                                    # and the no-response job moves nothing
    assert b"Start lead routing" in admin_client.get(reverse("ict:dashboard")).content
    client.force_login(make_user("sm-routing@example.com", R.SALES_MANAGER))
    assert b"Automatic assignment is off" in client.get(reverse("sales:assign_leads")).content

    client.force_login(admin_user)
    client.post(reverse("ict:routing_toggle"), {"next": "/ict/"})
    assert SalesSettings.get().auto_assign_new is True


def test_agents_cannot_flip_the_routing_switch(client, agent):
    from apps.sales.models import SalesSettings

    before = SalesSettings.get().auto_assign_new
    client.force_login(agent.user)
    assert client.post(reverse("ict:routing_toggle")).status_code == 403
    assert SalesSettings.get().auto_assign_new is before
