from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts import roles as R
from apps.hr import services as hr
from apps.hr.models import LeaveRequest, LeaveType, PublicHoliday
from apps.people.models import Employee
from apps.schedule import services as cal
from apps.schedule.models import CalendarEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def team(make_user):
    hr.ensure_defaults()
    boss_u = make_user("boss@example.com", R.SALES_MANAGER, full_name="Boss")
    boss = Employee.objects.create(user=boss_u, full_name="Boss")
    u = make_user("staff@example.com", R.SALES_AGENT, full_name="Staff")
    staff = Employee.objects.create(user=u, full_name="Staff", manager=boss)
    return boss, staff


def next_monday():
    d = timezone.localdate() + timedelta(days=14)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def test_working_days_skip_weekends_and_holidays():
    hr.ensure_defaults()
    mon = date(2031, 3, 3)
    assert hr.count_days(mon, mon + timedelta(days=6)) == 5
    PublicHoliday.objects.create(date=mon + timedelta(days=1), name="Test")
    assert hr.count_days(mon, mon + timedelta(days=6)) == 4
    assert hr.count_days(mon, mon, half_day="am") == Decimal("0.5")


def test_request_approve_updates_balance_and_calendar(team):
    boss, staff = team
    annual = LeaveType.objects.get(code="annual")
    m = next_monday()
    req = hr.request_leave(staff, annual, m, m + timedelta(days=2))
    assert hr.balance(staff, annual)["pending"] == 3
    hr.decide(req, boss.user, True)
    assert hr.balance(staff, annual)["used"] == 3
    assert CalendarEvent.objects.filter(source=f"leave:{req.pk}").exists()


def test_cannot_approve_own_leave_but_ceo_can(team, make_user):
    boss, staff = team
    m = next_monday()
    req = hr.request_leave(staff, LeaveType.objects.get(code="annual"), m, m)
    with pytest.raises(PermissionError):
        hr.decide(req, staff.user, True)
    ceo = make_user("ceo@example.com", R.DIRECTOR)
    emp = Employee.objects.create(user=ceo, full_name="CEO")
    own = hr.request_leave(emp, LeaveType.objects.get(code="annual"), m, m)
    assert hr.decide(own, ceo, True).status == LeaveRequest.APPROVED


def test_overlap_balance_and_document_rules(team):
    boss, staff = team
    m = next_monday()
    annual = LeaveType.objects.get(code="annual")
    hr.request_leave(staff, annual, m, m)
    with pytest.raises(ValueError):
        hr.request_leave(staff, annual, m, m)
    with pytest.raises(ValueError):
        hr.request_leave(staff, annual, m + timedelta(days=7), m + timedelta(days=70))
    with pytest.raises(ValueError):
        hr.request_leave(staff, LeaveType.objects.get(code="sick"), m + timedelta(days=21), m + timedelta(days=21))


def test_cancel_flow(team):
    boss, staff = team
    m = next_monday()
    annual = LeaveType.objects.get(code="annual")
    req = hr.request_leave(staff, annual, m, m)
    hr.request_cancel(req, staff.user)
    assert req.status == LeaveRequest.WITHDRAWN
    req2 = hr.request_leave(staff, annual, m, m)
    hr.decide(req2, boss.user, True)
    hr.request_cancel(req2, staff.user, "plans changed")
    assert req2.status == LeaveRequest.CANCEL_REQ and hr.balance(staff, annual)["used"] == 1
    hr.decide_cancel(req2, boss.user, True)
    assert hr.balance(staff, annual)["used"] == 0
    assert not CalendarEvent.objects.filter(source=f"leave:{req2.pk}").exists()


def test_pages_render(client, team, admin_user):
    boss, staff = team
    client.force_login(staff.user)
    assert client.get("/hr/my/").status_code == 200
    assert client.get("/hr/approvals/").status_code == 200          # empty state: nobody reports to staff
    assert client.get("/hr/").status_code in (302, 403)
    client.force_login(boss.user)
    assert client.get("/hr/approvals/").status_code == 200
    client.force_login(admin_user)
    for u in ("/hr/", "/hr/settings/", "/hr/approvals/", "/schedule/"):
        assert client.get(u).status_code == 200


def test_calendar_rank_and_invites(make_user):
    ceo = make_user("c@example.com", R.DIRECTOR)
    mgr = make_user("m@example.com", R.SALES_MANAGER)
    staff = make_user("s@example.com", R.SALES_AGENT)
    assert cal.calendar_rank(ceo) > cal.calendar_rank(mgr) > cal.calendar_rank(staff)
