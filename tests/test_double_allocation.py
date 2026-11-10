import pytest
from django.urls import reverse

from apps.accounts import roles as R
from apps.leads import services as lead_services
from apps.leads.models import Enquiry
from apps.people.models import Employee
from apps.sales import services
from apps.sales.models import DoubleAllocation


def _lead(phone, agent, source="whatsapp"):
    e = lead_services.create_enquiry(client_data={"full_name": "Same Guest", "phone": phone},
                                     enquiry_data={"source": source}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    return e


@pytest.mark.django_db
def test_same_guest_with_two_consultants_is_detected_and_resolved(agent, other_agent, make_user, client):
    first = _lead("0712345678", agent)
    services.log_call(first, agent.user, outcome="connected")           # agent contacted first
    second = _lead("0712345678", other_agent, "website")
    assert first.client_id == second.client_id
    assert services.detect_double_allocations() == 1
    assert services.detect_double_allocations() == 0                      # one open conflict per guest
    assert Enquiry.objects.filter(double_allocated=True).count() == 2
    da = DoubleAllocation.objects.get()
    assert services.suggested_owner(da.client) == agent
    mgr = make_user("mgr@example.com", R.SALES_MANAGER)
    client.force_login(mgr)
    assert client.get("/sales/double-allocation/").status_code == 200
    with pytest.raises(ValueError):
        services.resolve_double_allocation(da, Employee.objects.create(full_name="Outsider", is_sales_agent=True), mgr)
    client.post(reverse("sales:double_allocation_resolve", args=[da.pk]), {"agent": agent.pk})
    da.refresh_from_db(); second.refresh_from_db()
    assert da.status == "resolved" and da.kept_agent == agent and second.assigned_agent == agent
    assert not Enquiry.objects.filter(double_allocated=True).exists()
    assert services.detect_double_allocations() == 0


@pytest.mark.django_db
def test_agents_cannot_resolve_and_apply_suggested(agent, other_agent, client):
    _lead("0712345678", agent); _lead("0712345678", other_agent)
    services.detect_double_allocations()
    client.force_login(agent.user)
    assert client.get("/sales/double-allocation/").status_code == 403
    da = DoubleAllocation.objects.get()
    assert client.post(reverse("sales:double_allocation_resolve", args=[da.pk]), {"agent": agent.pk}).status_code == 403
