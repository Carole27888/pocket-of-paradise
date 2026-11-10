"""Executive tooling: central access model, Roles & Access board, System Logs, encrypted keys, private to-do."""
import json

import pytest
from django.contrib.auth.models import Group
from django.test.utils import override_settings
from django.urls import reverse

from apps.accounts import roles as R
from apps.core.modules import MODULES, modules_for
from apps.system import services
from apps.system.models import AuditLog, IntegrationCredential, TodoItem


@pytest.mark.django_db
def test_all_access_roles_open_every_module_but_ceo_is_read_only(make_user):
    for role in (R.ADMIN, R.DIRECTOR, R.COO):
        assert {m.key for m in modules_for(make_user(f"all-{role[:3]}@example.com", role))} == {m.key for m in MODULES}
    ceo, coo = make_user("ceo@example.com", R.DIRECTOR), make_user("coo@example.com", R.COO)
    assert ceo.is_read_only and not ceo.can_manage_finance and not ceo.can_manage_trips and not ceo.can_run_desk
    assert not coo.is_read_only and coo.can_manage_finance and coo.can_manage_trips and coo.can_manage_marketing and coo.can_run_desk
    assert coo.can_manage_sales and coo.sees_all_sales and coo.can_manage_suppliers and coo.can_manage_staff


@pytest.mark.django_db
def test_audit_service_records_actor_and_target(make_user):
    u = make_user("a@example.com", R.ADMIN)
    log = services.audit(u, "thing.done", u, "detail here")
    assert log.user == u and log.user_label == u.full_name and log.target_type == "User" and log.action == "thing.done"
    assert services.audit(None, "job.ran").user_label == "System"


# ------------------------------------------------------------------------------------------------ roles board
def _move(client, user, frm, to):
    return client.post(reverse("system:roles_move"), {"user": user.pk, "from": frm, "to": to})


@pytest.mark.django_db
def test_executives_move_people_and_every_change_is_logged(client, make_user):
    coo, staff = make_user("coo@example.com", R.COO), make_user("s@example.com", R.RECEPTIONIST)
    client.force_login(coo)
    assert client.get("/system/roles/").status_code == 200
    r = _move(client, staff, R.RECEPTIONIST, R.RESERVATIONS)
    assert r.json()["ok"] and set(staff.groups.values_list("name", flat=True)) == {R.RESERVATIONS}
    log = AuditLog.objects.get(action="role.changed")
    assert log.user == coo and log.target_label == staff.full_name and "Receptionist → Reservations" in log.detail
    assert staff.notifications.filter(title="Your role changed").exists()


@pytest.mark.django_db
def test_coo_cannot_create_or_touch_a_ceo_or_change_own_role(client, make_user):
    coo, ceo, staff = make_user("coo@example.com", R.COO), make_user("ceo@example.com", R.DIRECTOR), make_user("s@example.com", R.FINANCE)
    client.force_login(coo)
    assert _move(client, staff, R.FINANCE, R.DIRECTOR).status_code == 403          # no promotion to CEO
    assert _move(client, ceo, R.DIRECTOR, R.FINANCE).status_code == 403            # can't demote the CEO
    assert _move(client, coo, R.COO, R.ADMIN).status_code == 403                   # no self-promotion
    assert ceo.has_role(R.DIRECTOR) and not staff.has_role(R.DIRECTOR)
    assert not AuditLog.objects.filter(action="role.changed").exists()
    client.force_login(ceo)
    assert _move(client, staff, R.FINANCE, R.DIRECTOR).json()["ok"]                 # the CEO can
    assert _move(client, ceo, R.DIRECTOR, R.COO).status_code == 403                 # but never remove their own CEO role


@pytest.mark.django_db
def test_board_is_executives_only(client, make_user):
    for role in (R.ADMIN, R.SALES_MANAGER, R.FINANCE):
        client.force_login(make_user(f"{role[:4]}@example.com", role))
        assert client.get("/system/roles/").status_code == 403
        assert client.post(reverse("system:roles_move"), {"user": 1, "to": R.COO}).status_code == 403


# ------------------------------------------------------------------------------------------------ logs
@pytest.mark.django_db
def test_system_logs_access_and_filtering(client, make_user):
    services.audit(None, "import.committed", label="Clients batch")
    services.audit(None, "role.changed", label="Someone")
    client.force_login(make_user("fin@example.com", R.FINANCE))
    assert client.get("/system/logs/").status_code == 403
    for role in (R.ADMIN, R.DIRECTOR, R.COO):
        client.force_login(make_user(f"{role[:3]}2@example.com", role))
        r = client.get("/system/logs/", {"q": "Clients"})
        assert r.status_code == 200 and b"Clients batch" in r.content and b"Someone" not in r.content


# ------------------------------------------------------------------------------------------------ keys
@pytest.mark.django_db
@override_settings(INTEGRATIONS_MODE="mock")
def test_credentials_are_encrypted_masked_and_blank_keeps_secret(client, make_user):
    admin = make_user("adm@example.com", R.ADMIN)
    client.force_login(admin)
    client.post(reverse("system:credential_save", args=["whatsapp"]),
                {"access_token": "EAAB-secret-token-1234", "phone_number_id": "1055", "verify_token": "vt"})
    row = IntegrationCredential.objects.get(provider="whatsapp")
    from django.db import connection

    with connection.cursor() as c:
        c.execute("select payload from system_integrationcredential")
        raw = c.fetchone()[0]
    assert raw.startswith("enc$") and "secret-token" not in raw and row.values["access_token"] == "EAAB-secret-token-1234"
    page = client.get("/system/keys/").content.decode()
    assert "EAAB-secret-token-1234" not in page and "••••1234" in page
    client.post(reverse("system:credential_save", args=["whatsapp"]), {"access_token": "", "phone_number_id": "2077"})
    row.refresh_from_db()
    assert row.values["access_token"] == "EAAB-secret-token-1234" and row.values["phone_number_id"] == "2077"
    assert AuditLog.objects.filter(action="credential.saved").count() == 2
    assert "secret-token" not in "".join(AuditLog.objects.values_list("detail", flat=True))


@pytest.mark.django_db
def test_saved_key_beats_environment_and_takes_effect_immediately(monkeypatch):
    monkeypatch.setenv("AT_API_KEY", "from-env")
    assert services.get_credential("sms", "api_key") == "from-env"
    services.save_credentials("sms", {"api_key": "from-db"})
    assert services.get_credential("sms", "api_key") == "from-db"
    services.delete_credentials("sms")
    assert services.get_credential("sms", "api_key") == "from-env"


@pytest.mark.django_db
@override_settings(INTEGRATIONS_MODE="live", INTEGRATIONS_OFFLINE=False)
def test_key_test_reports_the_real_error(monkeypatch, make_user):
    services.save_credentials("ai", {"api_key": "sk-bad"})
    calls = []

    def fake(url, headers=None, data=None, method="GET", timeout=15):
        calls.append(url)
        return 401, json.dumps({"error": {"message": "invalid x-api-key"}})

    monkeypatch.setattr(services, "_http", fake)
    ok, msg = services.test_credentials("ai", make_user("t@example.com", R.ADMIN))
    assert not ok and "Invalid or unauthorised" in msg and "invalid x-api-key" in msg and calls[0].startswith("https://api.anthropic.com")
    monkeypatch.setattr(services, "_http", lambda *a, **k: (429, json.dumps({"error": {"message": "Your credit balance is too low"}})))
    ok, msg = services.test_credentials("ai")
    assert not ok and "no quota or billing" in msg
    monkeypatch.setattr(services, "_http", lambda *a, **k: (200, "{}"))
    assert services.test_credentials("ai")[0] is True
    row = IntegrationCredential.objects.get(provider="ai")
    assert row.last_test_ok is True and row.last_test_at


@pytest.mark.django_db
@override_settings(INTEGRATIONS_MODE="mock")
def test_key_test_in_mock_mode_makes_no_call(monkeypatch):
    monkeypatch.setattr(services, "_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in mock mode")))
    services.save_credentials("ai", {"api_key": "sk-x"})
    ok, msg = services.test_credentials("ai")
    assert ok and "Offline mode" in msg


@pytest.mark.django_db
def test_who_may_open_keys(client, make_user):
    for role, code in ((R.ADMIN, 200), (R.COO, 200), (R.DIRECTOR, 200), (R.ICT_OFFICER, 200), (R.FINANCE, 403), (R.SALES_AGENT, 403)):
        client.force_login(make_user(f"{role[:5]}{code}@example.com", role))
        assert client.get("/system/keys/").status_code == code, role


# ------------------------------------------------------------------------------------------------ to-do + api
@pytest.mark.django_db
def test_todo_is_private_even_from_admin_and_ceo(client, make_user):
    me, ceo = make_user("me@example.com", R.SALES_AGENT), make_user("ceo@example.com", R.DIRECTOR)
    client.force_login(me)
    client.post(reverse("system:todo_add"), {"text": "Call the lodge", "due": "2026-12-01"})
    item = TodoItem.objects.get()
    assert b"Call the lodge" in client.get("/system/todo/").content
    for viewer in (make_user("adm@example.com", R.ADMIN), ceo):
        client.force_login(viewer)
        assert b"Call the lodge" not in client.get("/system/todo/").content
        assert client.post(reverse("system:todo_action", args=[item.pk]), {"action": "remove"}).status_code == 404
    client.force_login(me)
    client.post(reverse("system:todo_action", args=[item.pk]), {"action": "done"})
    item.refresh_from_db()
    assert item.done_at
    client.post(reverse("system:todo_action", args=[item.pk]), {"action": "undo"})
    client.post(reverse("system:todo_action", args=[item.pk]), {"action": "remove"})
    assert not TodoItem.objects.exists()


@pytest.mark.django_db
def test_api_me(client, make_user):
    u = make_user("api@example.com", R.SALES_AGENT, full_name="Api User")
    client.force_login(u)
    j = client.get("/api/v1/me/").json()
    assert j["email"] == "api@example.com" and j["roles"] == [R.SALES_AGENT] and j["modules"] == ["sales", "me"] and j["read_only"] is False
