import pytest
from django.contrib.auth.models import Group

from apps.accounts import roles as R
from apps.accounts.models import User
from apps.people.models import Employee


@pytest.fixture
def make_user(db):
    def _make(email="u@example.com", *role_names, **extra):
        u = User.objects.create_user(email, "pass12345", full_name=extra.pop("full_name", email.split("@")[0].title()),
                                     **extra)
        for r in role_names:
            u.groups.add(Group.objects.get_or_create(name=r)[0])
        return u
    return _make


@pytest.fixture
def admin_user(make_user):
    return make_user("admin@example.com", R.ADMIN)


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def agent(make_user):
    u = make_user("agent@example.com", R.SALES_AGENT, full_name="Sarah Njeri")
    emp = Employee.objects.create(user=u, full_name="Sarah Njeri", is_sales_agent=True)
    return emp


@pytest.fixture
def other_agent(make_user):
    u = make_user("agent2@example.com", R.SALES_AGENT, full_name="James Kariuki")
    return Employee.objects.create(user=u, full_name="James Kariuki", is_sales_agent=True)
