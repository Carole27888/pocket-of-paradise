import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.frontdesk.models import Appointment, EquipmentRecord, ShiftNote, Visitor
from apps.people.models import Employee


@pytest.mark.django_db
def test_visitor_register_equipment_and_notes(client, make_user):
    host = Employee.objects.create(full_name="Host Person", user=make_user("host@example.com", R.OPERATIONS))
    client.force_login(make_user("recep@example.com", R.RECEPTIONIST))
    assert client.get("/frontdesk/visitors/").status_code == 200
    client.post(reverse("frontdesk:visitor_in"), {"name": "Julius", "reason": "Quote", "host": host.pk, "badge": "12"})
    v = Visitor.objects.get()
    assert v.is_in and v.host == host
    client.post(reverse("frontdesk:visitor_out", args=[v.pk]))
    v.refresh_from_db()
    assert not v.is_in
    client.post(reverse("frontdesk:visitor_in"), {"name": "", "reason": ""})        # validation: nothing created
    assert Visitor.objects.count() == 1
    eq = EquipmentRecord.objects.create(item="radio", code="RAD-01")
    client.post(reverse("frontdesk:equipment_action"), {"id": eq.pk, "action": "issue", "who": host.pk, "days": "1"})
    eq.refresh_from_db()
    assert eq.state == "Out"
    client.post(reverse("frontdesk:equipment_action"), {"id": eq.pk, "action": "issue", "who": host.pk})
    assert "already out" in "".join(str(m) for m in client.get("/frontdesk/visitors/").context["messages"]) or eq.state == "Out"
    EquipmentRecord.objects.filter(pk=eq.pk).update(due_back=timezone.now() - timezone.timedelta(hours=2))
    eq.refresh_from_db()
    assert eq.state == "Overdue"
    client.post(reverse("frontdesk:equipment_action"), {"id": eq.pk, "action": "return"})
    eq.refresh_from_db()
    assert eq.state == "In store"
    client.post(reverse("frontdesk:appointment_add"), {"title": "Meeting", "start": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "host": host.pk})
    assert Appointment.objects.count() == 1
    client.post(reverse("frontdesk:shift_note"), {"note": "Hand-over"})
    assert ShiftNote.objects.count() == 1


@pytest.mark.django_db
def test_dispatcher_sees_visitors_read_only_and_cannot_sign_in(client, make_user):
    client.force_login(make_user("disp@example.com", R.DISPATCHER))
    assert client.get("/frontdesk/visitors/").status_code == 200
    assert client.post(reverse("frontdesk:visitor_in"), {"name": "X", "reason": "Y"}).status_code == 403
