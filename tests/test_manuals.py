import pytest

from apps.accounts import roles as R
from apps.manuals import content

pytestmark = pytest.mark.django_db


def test_manuals_follow_role(make_user, client):
    agent = make_user("a@example.com", R.SALES_AGENT)
    slugs = {m.slug for m in content.visible(agent)}
    assert {"getting-started", "my-space", "sales"} <= slugs
    assert not slugs & {"finance", "executive-guide", "admin"}
    assert "executive-guide" in {m.slug for m in content.visible(make_user("c@example.com", R.DIRECTOR))}
    client.force_login(agent)
    assert client.get("/help/").status_code == 200
    assert client.get("/help/sales/").status_code == 200
    assert client.get("/help/executive-guide/").status_code == 403
    assert client.get("/help/nope/").status_code == 404


def test_every_manual_renders_for_admin(admin_client):
    for m in content.MANUALS:
        assert admin_client.get(f"/help/{m.slug}/").status_code == 200, m.slug


def test_frontdesk_manual_is_rich(make_user, client):
    m = content.BY_SLUG["frontdesk"]
    secs = m.all_sections()
    assert len(secs) >= 8 and any(s.steps for s in secs) and any(s.tip for s in secs)
    assert not any("viewing" in (s.title + s.intro).lower() for s in secs)
    client.force_login(make_user("fd@example.com", R.FRONTDESK_MANAGER))
    r = client.get("/frontdesk/manual/")
    assert r.status_code == 200 and b"Capturing a New Lead" in r.content
