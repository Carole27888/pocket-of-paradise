import pytest
from django.test.utils import override_settings
from django.urls import reverse

from apps.accounts import roles as R
from apps.accounts import twofactor
from apps.system.models import AuditLog


def test_totp_matches_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                      # ASCII "12345678901234567890" in base32
    assert twofactor.totp_at(secret, 59, step=30)[-6:] == "287082"       # RFC 6238 appendix B (SHA-1), T=59
    assert twofactor.totp_at(secret, 1111111109)[-6:] == "081804"
    assert twofactor.totp_at(secret, 20000000000)[-6:] == "353130"


def test_verify_window_replay_and_junk():
    s = twofactor.new_secret()
    now = 1_700_000_000
    code = twofactor.totp_at(s, now)
    c = twofactor.verify(s, code, now=now)
    assert c == now // 30
    assert twofactor.verify(s, code, last_counter=c, now=now) is None          # a code can't be reused
    assert twofactor.verify(s, twofactor.totp_at(s, now - 30), now=now) is not None   # one step of clock drift allowed
    assert twofactor.verify(s, twofactor.totp_at(s, now - 120), now=now) is None
    for bad in ("", "abc123", "12345", "1234567", None):
        assert twofactor.verify(s, bad, now=now) is None


def _next_code(secret):
    """A valid code from the next 30-second window (the enrolment code can't be replayed)."""
    import time

    return twofactor.totp_at(secret, time.time() + 30)


def _enroll(client, user):
    r = client.get(reverse("accounts:security"))
    secret = client.session["totp_setup"]
    r = client.post(reverse("accounts:security"), {"action": "enable", "code": twofactor.totp_at(secret)})
    user.refresh_from_db()
    return secret, r


@pytest.mark.django_db
@override_settings(TWO_FACTOR_ENABLED=True)
def test_required_roles_are_forced_to_set_up_others_are_not(client, make_user):
    fin, agent = make_user("fin@example.com", R.FINANCE), make_user("ag@example.com", R.SALES_AGENT)
    client.force_login(fin)
    assert client.get("/finance/").status_code == 302 and client.get("/finance/").url == reverse("accounts:security")
    assert client.get(reverse("accounts:security")).status_code == 200
    client.force_login(agent)
    assert client.get("/sales/").status_code == 200                              # optional for sales


@pytest.mark.django_db
@override_settings(TWO_FACTOR_ENABLED=True)
def test_enrol_then_sign_in_needs_the_code(client, make_user):
    u = make_user("adm@example.com", R.ADMIN)
    client.force_login(u)
    secret, r = _enroll(client, u)
    assert u.totp_enabled and b"recovery codes" in r.content.lower() and len(u.recovery_hashes) == 8
    assert AuditLog.objects.filter(action="2fa.enabled").exists()
    client.post(reverse("accounts:logout"))
    r = client.post(reverse("accounts:login"), {"email": "adm@example.com", "password": "pass12345"})
    assert r.status_code == 302 and r.url == reverse("accounts:two_factor") and "_auth_user_id" not in client.session
    bad = client.post(reverse("accounts:two_factor"), {"code": "000000"})
    assert bad.status_code == 200 and b"isn" in bad.content and "_auth_user_id" not in client.session
    assert AuditLog.objects.filter(action="2fa.failed").exists()
    ok = client.post(reverse("accounts:two_factor"), {"code": _next_code(secret)})
    assert ok.status_code == 302 and client.session["_auth_user_id"] == str(u.pk)


@pytest.mark.django_db
@override_settings(TWO_FACTOR_ENABLED=True)
def test_recovery_code_works_once(client, make_user):
    u = make_user("adm@example.com", R.ADMIN)
    client.force_login(u)
    secret, r = _enroll(client, u)
    plain = [c for c in __import__("re").findall(r"[0-9a-f]{4}-[0-9a-f]{4}", r.content.decode())]
    assert len(plain) == 8
    client.post(reverse("accounts:logout"))
    for expected in (True, False):
        client.post(reverse("accounts:login"), {"email": "adm@example.com", "password": "pass12345"})
        r = client.post(reverse("accounts:two_factor"), {"code": plain[0]})
        assert (r.status_code == 302) is expected
        client.post(reverse("accounts:logout"))


@pytest.mark.django_db
@override_settings(TWO_FACTOR_ENABLED=True, LOGIN_RATE_LIMIT=3)
def test_two_factor_attempts_are_rate_limited(client, make_user):
    from django.core.cache import cache

    cache.clear()
    u = make_user("adm@example.com", R.ADMIN)
    client.force_login(u)
    secret, _ = _enroll(client, u)
    client.post(reverse("accounts:logout"))
    client.post(reverse("accounts:login"), {"email": "adm@example.com", "password": "pass12345"})
    for _ in range(3):
        client.post(reverse("accounts:two_factor"), {"code": "111111"})
    r = client.post(reverse("accounts:two_factor"), {"code": _next_code(secret)})      # even the right code is refused now
    assert b"Too many attempts" in r.content and "_auth_user_id" not in client.session


@pytest.mark.django_db
@override_settings(TWO_FACTOR_ENABLED=True)
def test_optional_users_can_disable_required_cannot_and_admin_can_reset(client, make_user):
    agent, admin = make_user("ag@example.com", R.SALES_AGENT), make_user("adm@example.com", R.ADMIN)
    client.force_login(agent)
    secret, _ = _enroll(client, agent)
    r = client.post(reverse("accounts:security"), {"action": "disable", "code": twofactor.totp_at(secret, __import__("time").time() + 30)})
    agent.refresh_from_db()
    assert not agent.totp_enabled
    client.force_login(admin)
    secret, _ = _enroll(client, admin)
    client.post(reverse("accounts:security"), {"action": "disable", "code": twofactor.totp_at(secret, __import__("time").time() + 30)})
    admin.refresh_from_db()
    assert admin.totp_enabled                                                    # a required role can't turn it off
    assert client.post(reverse("accounts:reset_2fa", args=[agent.pk])).status_code == 302
    agent.refresh_from_db()
    assert AuditLog.objects.filter(action="2fa.reset").count() == 1


@pytest.mark.django_db
def test_two_factor_is_inert_when_the_flag_is_off(client, make_user):
    u = make_user("fin@example.com", R.FINANCE)
    client.force_login(u)
    assert client.get("/finance/").status_code == 200
    r = client.post(reverse("accounts:login"), {"email": "fin@example.com", "password": "pass12345"})
    assert r.url == "/"
