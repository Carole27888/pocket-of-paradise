"""Client ownership: round-robin on creation, sticky owner for returning clients."""
import pytest

from apps.accounts import roles as R
from apps.leads import services as lead_services
from apps.people.models import Employee
from apps.sales import services


def _enquiry(phone="0712345678", name="Wanjiru Kamau", **kw):
    return lead_services.create_enquiry(
        client_data={"full_name": name, "phone": phone}, enquiry_data={"source": "whatsapp", **kw}, auto_assign=False)


@pytest.fixture
def manager(make_user):
    u = make_user("mgr@example.com", R.SALES_MANAGER, full_name="Joyce Nduta")
    Employee.objects.create(user=u, full_name="Joyce Nduta")
    return u


# ------------------------------------------------------------------------------------- client ownership
def _new_client(client, **extra):
    data = {"full_name": "New Person", "phone": "0712345001", "nationality": "Kenyan", "preferred_language": "English",
            **extra}
    return client.post("/clients/new/", data)


@pytest.mark.django_db
def test_new_clients_rotate_between_agents(client, agent, other_agent, manager):
    from apps.contacts.models import Client

    client.force_login(manager)
    owners = []
    for i in range(4):
        _new_client(client, full_name=f"P{i}", phone=f"07123450{i:02d}")
        owners.append(Client.objects.get(full_name=f"P{i}").owner_id)
    assert set(owners) == {agent.pk, other_agent.pk}
    assert owners[0] != owners[1] and owners[0] == owners[2] and owners[1] == owners[3]


@pytest.mark.django_db
def test_explicit_owner_is_kept_and_visible_to_that_agent_only(client, agent, other_agent, manager):
    from apps.contacts.models import Client

    client.force_login(manager)
    _new_client(client, owner=other_agent.pk)
    c = Client.objects.get(full_name="New Person")
    assert c.owner == other_agent and c.owner_assigned_at
    client.force_login(other_agent.user)
    assert client.get(c.get_absolute_url()).status_code == 200
    assert "New Person" in client.get("/clients/").content.decode()
    client.force_login(agent.user)
    assert client.get(c.get_absolute_url()).status_code == 404


@pytest.mark.django_db
def test_returning_client_goes_back_to_their_owner(agent, other_agent):
    e1 = _enquiry("0711000077")
    lead_services.assign_agent(e1, other_agent, None, "manual")      # first agent becomes owner
    e1.client.refresh_from_db()
    assert e1.client.owner == other_agent
    e2 = _enquiry("0711000077")                                  # same person enquires again
    a, why = services.auto_assign(e2, None)
    assert a == other_agent and why == "Client owner"
