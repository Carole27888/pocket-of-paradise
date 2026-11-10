"""Front Desk department roles: who may capture enquiries, who may dispatch trips."""
import pytest

from apps.accounts import roles as R
from apps.core.modules import modules_for
from apps.people.models import Department


@pytest.mark.django_db
def test_front_desk_department_is_created_by_the_migration():
    """The migration creates the department on every new install (and is safe to re-run)."""
    import importlib

    from django.apps import apps

    mig = importlib.import_module("apps.people.migrations.0002_frontdesk_department")
    Department.objects.filter(name="Front Desk").delete()
    mig.add_department(apps, None)
    mig.add_department(apps, None)
    assert Department.objects.filter(name="Front Desk").count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("role,capture,move,manage", [
    (R.FRONTDESK_MANAGER, True, True, True),
    (R.RESERVATIONS, True, True, False),
    (R.RECEPTIONIST, True, False, False),
    (R.DISPATCHER, False, True, False),
])
def test_desk_role_permissions(make_user, role, capture, move, manage):
    u = make_user(f"{role[:4].lower()}@example.com", role)
    assert u.can_run_desk is capture and u.can_run_movement is move and u.can_manage_desk is manage
    assert "frontdesk" in [m.key for m in modules_for(u)]
    assert "sales" not in [m.key for m in modules_for(u)]


@pytest.mark.django_db
def test_desk_urls_enforce_split(client, make_user):
    recep, disp = make_user("r@example.com", R.RECEPTIONIST), make_user("d@example.com", R.DISPATCHER)
    client.force_login(recep)
    assert client.get("/frontdesk/").status_code == 200
    assert client.get("/frontdesk/movement/check-out/").status_code == 403
    client.force_login(disp)
    assert client.get("/frontdesk/movement/check-out/").status_code == 200
    assert client.post("/frontdesk/queues/", {"full_name": "X", "phone": "0712345678", "source": "walk_in"}).status_code == 403


@pytest.mark.django_db
def test_desk_roles_are_registered_groups_with_descriptions():
    from django.contrib.auth.models import Group

    assert R.FRONTDESK_ROLES <= set(R.ALL_ROLES) and R.FRONTDESK_ROLES <= set(R.ROLE_DESCRIPTIONS)
    assert R.FRONTDESK_ROLES <= set(Group.objects.values_list("name", flat=True))


@pytest.mark.django_db
@pytest.mark.parametrize("role,ok", [(R.RECEPTIONIST, True), (R.SALES_MANAGER, True), (R.RESERVATIONS, True),
                                     (R.SALES_AGENT, False), (R.DISPATCHER, False), (R.DIRECTOR, False)])
def test_who_can_add_clients(client, make_user, role, ok):
    client.force_login(make_user("c@example.com", role))
    assert client.get("/clients/new/").status_code == (200 if ok else 403)
    r = client.post("/clients/new/", {"full_name": "New Person", "phone": "0712345001", "nationality": "Kenyan",
                                      "preferred_language": "English"})
    from apps.contacts.models import Client
    assert Client.objects.filter(full_name="New Person").exists() is ok
