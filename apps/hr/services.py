"""Leave: working-day maths, derived balances, request → approval → cancellation, carry-forward and analytics."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts import roles as R
from apps.notifications.services import notify, notify_roles
from apps.schedule import services as calendar

from .models import LeaveEntitlement, LeavePolicy, LeaveRequest, LeaveType, PublicHoliday

D = Decimal

DEFAULT_TYPES = [  # code, name, paid, capped, days, requires_doc, statutory, calendar_days
    ("annual", "Annual leave", True, True, "21", False, True, False),
    ("sick", "Sick leave", True, True, "14", True, True, False),
    ("maternity", "Maternity leave", True, True, "90", True, True, True),
    ("paternity", "Paternity leave", True, True, "14", True, True, True),
    ("compassionate", "Compassionate leave", True, True, "5", False, False, False),
    ("study", "Study / exam leave", True, True, "5", True, False, False),
    ("unpaid", "Unpaid leave", False, False, "0", False, False, False),
]
# Fixed-date Kenyan public holidays plus the Easter dates for 2026-27. Admin edits the list: confirm each year (Eid etc. are gazetted late).
DEFAULT_HOLIDAYS = [("New Year's Day", 1, 1), ("Labour Day", 5, 1), ("Madaraka Day", 6, 1), ("Utamaduni Day", 10, 10), ("Mashujaa Day", 10, 20),
                    ("Jamhuri Day", 12, 12), ("Christmas Day", 12, 25), ("Boxing Day", 12, 26)]
EASTER = [("Good Friday", date(2026, 4, 3)), ("Easter Monday", date(2026, 4, 6)), ("Good Friday", date(2027, 3, 26)), ("Easter Monday", date(2027, 3, 29))]


def ensure_defaults():
    for i, (code, name, paid, capped, days, doc, stat, cal) in enumerate(DEFAULT_TYPES):
        LeaveType.objects.get_or_create(code=code, defaults=dict(name=name, paid=paid, capped=capped, default_days=D(days), requires_document=doc,
                                                                 statutory=stat, calendar_days=cal, sort_order=i))
    LeavePolicy.get()
    if not PublicHoliday.objects.exists():
        year = timezone.localdate().year
        for y in (year, year + 1):
            for name, m, d in DEFAULT_HOLIDAYS:
                PublicHoliday.objects.get_or_create(date=date(y, m, d), defaults={"name": name})
        for name, d in EASTER:
            PublicHoliday.objects.get_or_create(date=d, defaults={"name": name})


def leave_year(d: date, policy: LeavePolicy | None = None) -> int:
    start = (policy or LeavePolicy.get()).year_start_month
    return d.year if d.month >= start else d.year - 1


def year_bounds(year: int, policy: LeavePolicy | None = None):
    start = (policy or LeavePolicy.get()).year_start_month
    first = date(year, start, 1)
    return first, date(year + 1, start, 1) - timedelta(days=1)


def count_days(start: date, end: date, *, calendar_days=False, half_day="") -> D:
    """Working days (Mon–Fri, minus public holidays) or every calendar day; a half day is 0.5 and only for a single day."""
    if end < start:
        raise ValueError("The end date can't be before the start date.")
    if half_day and start != end:
        raise ValueError("A half day can only be requested for a single day.")
    holidays = set(PublicHoliday.objects.filter(date__range=(start, end)).values_list("date", flat=True))
    n, d = 0, start
    while d <= end:
        if calendar_days or (d.weekday() < 5 and d not in holidays):
            n += 1
        d += timedelta(days=1)
    return D("0.5") if half_day and n else D(n)


def balance(employee, leave_type: LeaveType, year: int | None = None) -> dict:
    """Always derived: entitlement + carried forward − approved days. Never a stored running total."""
    year = year if year is not None else leave_year(timezone.localdate())
    ent = LeaveEntitlement.objects.filter(employee=employee, leave_type=leave_type, year=year).first()
    entitled = ent.days if ent else leave_type.default_days
    carried = ent.carried_forward if ent else D("0")
    first, last = year_bounds(year)
    reqs = LeaveRequest.objects.filter(employee=employee, leave_type=leave_type, start_date__range=(first, last))
    used = sum((r.days for r in reqs.filter(status__in=LeaveRequest.COUNTED)), D("0"))
    pending = sum((r.days for r in reqs.filter(status=LeaveRequest.PENDING)), D("0"))
    total = entitled + carried
    return {"entitled": entitled, "carried": carried, "total": total, "used": used, "pending": pending,
            "remaining": total - used, "available": total - used - pending, "capped": leave_type.capped, "year": year}


def approvers_for(employee) -> list:
    """The employee's manager; failing that, the top roles (never the employee themself)."""
    out = []
    if employee.manager_id and employee.manager.user_id:
        out.append(employee.manager.user)
    if not out:
        from apps.accounts.models import User

        out = list(User.objects.filter(is_active=True, groups__name__in=[R.ADMIN, R.COO, R.DIRECTOR]).distinct())
    return [u for u in out if u.pk != employee.user_id] or out


def can_decide(user, leave: LeaveRequest) -> bool:
    """Self-approval is blocked for everyone except the CEO (nobody sits above them)."""
    own = leave.employee.user_id == user.pk
    if own:
        return user.has_role(R.DIRECTOR)
    mgr = leave.employee.manager
    if mgr and mgr.user_id == user.pk:
        return True
    if user.is_superuser or user.has_role(R.ADMIN, R.COO, R.DIRECTOR):
        return True
    return False


@transaction.atomic
def request_leave(employee, leave_type: LeaveType, start: date, end: date, *, half_day="", reason="", document=None, user=None) -> LeaveRequest:
    if not leave_type.active:
        raise ValueError("That leave type is not available.")
    days = count_days(start, end, calendar_days=leave_type.calendar_days, half_day=half_day)
    if days <= 0:
        raise ValueError("Those dates have no working days (weekend or public holiday).")
    if leave_type.requires_document and not document:
        raise ValueError(f"{leave_type.name} needs a supporting document (e.g. a doctor's or birth certificate).")
    clash = LeaveRequest.objects.filter(employee=employee, status__in=[LeaveRequest.PENDING, LeaveRequest.APPROVED, LeaveRequest.CANCEL_REQ],
                                        start_date__lte=end, end_date__gte=start).exists()
    if clash:
        raise ValueError("You already have leave requested or approved on some of those dates.")
    if leave_type.capped:
        bal = balance(employee, leave_type, leave_year(start))
        if days > bal["available"]:
            raise ValueError(f"That's {days:g} day(s) but you only have {bal['available']:g} left of {leave_type.name.lower()}"
                             + (f" ({bal['pending']:g} more are awaiting approval)." if bal["pending"] else "."))
    req = LeaveRequest.objects.create(employee=employee, leave_type=leave_type, start_date=start, end_date=end, half_day=half_day, days=days,
                                      reason=reason[:240], document=document or "", created_by=user)
    for a in approvers_for(employee):
        notify(a, f"Leave request: {employee.full_name}", f"{leave_type.name} · {start:%d %b}–{end:%d %b} ({days:g} days)", "/hr/approvals/", "info", dedupe_key=f"leave-req:{req.pk}:{a.pk}")
    return req


@transaction.atomic
def decide(leave: LeaveRequest, approver, approve: bool, note="") -> LeaveRequest:
    if leave.status != LeaveRequest.PENDING:
        raise ValueError("This request has already been decided.")
    if not can_decide(approver, leave):
        raise PermissionError("You can't approve this request." if leave.employee.user_id != approver.pk else "You can't approve your own leave.")
    leave.status = LeaveRequest.APPROVED if approve else LeaveRequest.REJECTED
    leave.approver, leave.decided_at, leave.decision_note = approver, timezone.now(), note[:240]
    leave.save()
    calendar.sync_leave_event(leave)
    if leave.employee.user_id:
        notify(leave.employee.user, f"Leave {'approved' if approve else 'declined'}", f"{leave.leave_type.name} · {leave.start_date:%d %b}–{leave.end_date:%d %b}"
               + (f" · {note}" if note else ""), "/hr/my/", "success" if approve else "warning", dedupe_key=f"leave-dec:{leave.pk}")
    return leave


@transaction.atomic
def request_cancel(leave: LeaveRequest, user, reason="") -> LeaveRequest:
    """Pending leave is withdrawn at once; approved leave asks the approver to confirm."""
    if leave.employee.user_id != user.pk:
        raise PermissionError("You can only cancel your own leave.")
    if leave.status == LeaveRequest.PENDING:
        leave.status = LeaveRequest.WITHDRAWN
        leave.save(update_fields=["status", "updated_at"])
    elif leave.status == LeaveRequest.APPROVED:
        if leave.start_date < timezone.localdate() and leave.end_date < timezone.localdate():
            raise ValueError("That leave has already been taken.")
        leave.status, leave.cancel_reason = LeaveRequest.CANCEL_REQ, reason[:240]
        leave.save(update_fields=["status", "cancel_reason", "updated_at"])
        targets = [leave.approver] if leave.approver_id else approvers_for(leave.employee)
        for a in targets:
            notify(a, f"Cancellation request: {leave.employee.full_name}", f"{leave.leave_type.name} · {leave.start_date:%d %b}", "/hr/approvals/", "info", dedupe_key=f"leave-cx:{leave.pk}:{a.pk}")
    else:
        raise ValueError("This request can't be cancelled.")
    return leave


@transaction.atomic
def decide_cancel(leave: LeaveRequest, approver, confirm: bool) -> LeaveRequest:
    if leave.status != LeaveRequest.CANCEL_REQ:
        raise ValueError("There is no cancellation waiting on this request.")
    if not can_decide(approver, leave) and leave.approver_id != approver.pk:
        raise PermissionError("You can't decide this cancellation.")
    leave.status = LeaveRequest.CANCELLED if confirm else LeaveRequest.APPROVED
    leave.save(update_fields=["status", "updated_at"])
    calendar.sync_leave_event(leave)               # cancelled → calendar entry removed; denied → stays
    if leave.employee.user_id:
        notify(leave.employee.user, "Leave cancellation " + ("confirmed" if confirm else "declined"), f"{leave.leave_type.name} · {leave.start_date:%d %b}", "/hr/my/", "info",
               dedupe_key=f"leave-cxd:{leave.pk}:{leave.status}")
    return leave


@transaction.atomic
def carry_forward(to_year: int) -> int:
    """Roll unused days of the carry-forward type into `to_year`, capped by policy. Safe to re-run."""
    pol = LeavePolicy.get()
    lt = LeaveType.objects.filter(code=pol.carry_forward_type).first()
    if lt is None:
        return 0
    from apps.people.models import Employee

    n = 0
    for emp in Employee.objects.filter(is_active=True):
        prev = balance(emp, lt, to_year - 1)
        carry = min(max(prev["remaining"], D("0")), pol.carry_forward_cap)
        ent, created = LeaveEntitlement.objects.get_or_create(employee=emp, leave_type=lt, year=to_year, defaults={"days": lt.default_days, "carried_forward": carry})
        if not created and ent.carried_forward != carry:
            ent.carried_forward = carry
            ent.save(update_fields=["carried_forward"])
        n += created
    return n


def on_leave_today(today=None):
    today = today or timezone.localdate()
    return LeaveRequest.objects.filter(status__in=LeaveRequest.COUNTED, start_date__lte=today, end_date__gte=today).select_related("employee", "leave_type")


def analytics(year: int, department_id=None, type_id=None) -> dict:
    qs = LeaveRequest.objects.filter(status__in=LeaveRequest.COUNTED, start_date__range=year_bounds(year)).select_related("employee__department", "leave_type")
    if department_id:
        qs = qs.filter(employee__department_id=department_id)
    if type_id:
        qs = qs.filter(leave_type_id=type_id)
    by_type, by_month, by_dept, by_emp = defaultdict(D), defaultdict(D), defaultdict(D), defaultdict(D)
    for r in qs:
        by_type[r.leave_type.name] += r.days
        by_month[r.start_date.month] += r.days
        by_dept[r.employee.department.name if r.employee.department else "No department"] += r.days
        by_emp[r.employee.full_name] += r.days
    return {"total_days": sum(by_type.values(), D("0")), "requests": qs.count(), "by_type": dict(by_type), "by_month": dict(by_month),
            "by_dept": dict(by_dept), "top": sorted(by_emp.items(), key=lambda kv: -kv[1])[:8],
            "pending": LeaveRequest.objects.filter(status=LeaveRequest.PENDING).count()}



# ------------------------------------------------------------------ attendance (clock in / out with location)
import math

from .models import AttendancePolicy, AttendanceRecord, OfficeLocation


def distance_m(lat1, lng1, lat2, lng2) -> int:
    """Great-circle distance in metres (haversine)."""
    r = 6371000.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp, dl = p2 - p1, math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(round(2 * r * math.asin(math.sqrt(a))))


def nearest_office(lat, lng):
    best = None
    for o in OfficeLocation.objects.filter(active=True):
        d = distance_m(lat, lng, o.latitude, o.longitude)
        if best is None or d < best[1]:
            best = (o, d)
    return best            # (office, metres) or None


def _locate(lat, lng, accuracy):
    """-> (lat, lng, accuracy, office, distance, at_office); everything None/False when no usable location was shared."""
    try:
        lat, lng = (round(float(lat), 6), round(float(lng), 6)) if lat not in (None, "") and lng not in (None, "") else (None, None)
    except (TypeError, ValueError):
        lat = lng = None
    if lat is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, None, None, None, False
    try:
        acc = int(float(accuracy)) if accuracy not in (None, "") else None
    except (TypeError, ValueError):
        acc = None
    near = nearest_office(lat, lng)
    if near is None:
        return lat, lng, acc, None, None, False
    office, dist = near
    return lat, lng, acc, office, dist, dist <= office.radius_m


def today_record(user):
    return AttendanceRecord.objects.filter(user=user, date=timezone.localdate(), clock_out_at__isnull=True).first()


@transaction.atomic
def clock_in(user, lat=None, lng=None, accuracy=None, note=""):
    pol = AttendancePolicy.get()
    if today_record(user):
        raise ValueError("You are already clocked in.")
    la, ln, acc, office, dist, at_office = _locate(lat, lng, accuracy)
    if pol.require_location and la is None:
        raise ValueError("Share your location to clock in. Allow location access in your browser and try again.")
    if pol.require_office and not at_office:
        where = f" You are {dist:,} m from {office.name}." if office else ""
        raise ValueError("You can only clock in at the office." + where)
    now = timezone.localtime()
    from datetime import datetime, timedelta as td

    start = timezone.make_aware(datetime.combine(now.date(), pol.workday_start if hasattr(pol.workday_start, "hour") else datetime.strptime(str(pol.workday_start), "%H:%M").time()))
    late = now > start + td(minutes=pol.grace_minutes)
    return AttendanceRecord.objects.create(user=user, date=now.date(), clock_in_at=timezone.now(), in_lat=la, in_lng=ln, in_accuracy_m=acc, in_office=office,
                                           in_distance_m=dist, in_at_office=at_office, late=late, note=note[:160])


@transaction.atomic
def clock_out(user, lat=None, lng=None, accuracy=None):
    rec = AttendanceRecord.objects.filter(user=user, clock_out_at__isnull=True).order_by("-clock_in_at").first()
    if rec is None:
        raise ValueError("You are not clocked in.")
    la, ln, acc, office, dist, at_office = _locate(lat, lng, accuracy)
    if AttendancePolicy.get().require_location and la is None:
        raise ValueError("Share your location to clock out.")
    rec.clock_out_at, rec.out_lat, rec.out_lng, rec.out_accuracy_m, rec.out_distance_m, rec.out_at_office = timezone.now(), la, ln, acc, dist, at_office
    rec.save()
    return rec


def describe(rec) -> str:
    if rec.in_distance_m is None:
        return "Location not recorded" if rec.in_lat is None else "No office set up"
    return "At the office" if rec.in_at_office else f"{rec.in_distance_m:,} m from the office" if rec.in_distance_m < 1000 else f"{rec.in_distance_m / 1000:.1f} km from the office"
