"""Front Desk business logic: trip movement (check-out / check-in), capture queues and the daily desk report."""
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.fleet.models import Vehicle
from apps.leads.models import Enquiry, Source
from apps.people.models import Employee

from .models import DESK_SOURCES, TripMovement


class MovementError(ValueError):
    pass


def _busy(employee: Employee, exclude_pk=None) -> TripMovement | None:
    qs = TripMovement.objects.open().filter(Q(driver=employee) | Q(guide=employee))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


@transaction.atomic
def check_out(*, vehicle=None, driver=None, guide=None, expected_return_at, user=None, checked_out_at=None,
              odometer_out=None, **fields) -> TripMovement:
    """Send a vehicle and/or crew out. Marks the vehicle "Out on trip" and the crew "Out on trip"."""
    if not (vehicle or driver or guide):
        raise MovementError("Choose a vehicle, a driver or a guide to check out.")
    checked_out_at = checked_out_at or timezone.now()
    if expected_return_at <= checked_out_at:
        raise MovementError("Expected return must be after the check-out time.")
    if vehicle is not None:
        vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
        if vehicle.status != Vehicle.AVAILABLE:
            raise MovementError(f"{vehicle.registration_no} is {vehicle.get_status_display().lower()}, not available.")
        if odometer_out is None:
            odometer_out = vehicle.odometer_km
        elif odometer_out < vehicle.odometer_km:
            raise MovementError(f"Odometer reading is below the last recorded {vehicle.odometer_km:,} km "
                                f"for {vehicle.registration_no}.")
    crew = [p for p in (driver, guide) if p]
    for person in {p.pk: p for p in crew}.values():
        person = Employee.objects.select_for_update().get(pk=person.pk)
        if not person.is_available:
            raise MovementError(f"{person} is {person.get_on_duty_display().lower()} and can't be checked out.")
        busy = _busy(person)
        if busy:
            raise MovementError(f"{person} is already out on {busy.reference} ({busy.destination_label}).")
    mv = TripMovement.objects.create(vehicle=vehicle, driver=driver, guide=guide, checked_out_at=checked_out_at,
                                     expected_return_at=expected_return_at, checked_out_by=user,
                                     odometer_out=odometer_out if vehicle else None, **fields)
    if vehicle is not None:
        vehicle.status = Vehicle.OUT
        if driver:
            vehicle.current_driver = driver
        vehicle.odometer_km = max(vehicle.odometer_km, odometer_out or 0)
        vehicle.save(update_fields=["status", "current_driver", "odometer_km", "updated_at", "updated_by"])
    Employee.objects.filter(pk__in=[p.pk for p in crew]).update(on_duty=Employee.OUT_ON_TRIP,
                                                                updated_at=timezone.now())
    return mv


RETURN_STATUS = {TripMovement.CONDITION_OK: Vehicle.AVAILABLE, TripMovement.CONDITION_SERVICE: Vehicle.MAINTENANCE,
                 TripMovement.CONDITION_DAMAGED: Vehicle.OUT_OF_SERVICE}


@transaction.atomic
def check_in(movement: TripMovement, *, user=None, checked_in_at=None, odometer_in=None, fuel_in="",
             condition_in=TripMovement.CONDITION_OK, notes_in="") -> TripMovement:
    """Close a movement: vehicle back to available (or maintenance / out of service), crew back in office."""
    mv = TripMovement.objects.select_for_update().get(pk=movement.pk)
    if not mv.is_open:
        raise MovementError(f"{mv.reference} was already checked in {timezone.localtime(mv.checked_in_at):%d %b %H:%M}.")
    checked_in_at = checked_in_at or timezone.now()
    if checked_in_at < mv.checked_out_at:
        raise MovementError("Check-in time can't be before the check-out time.")
    if checked_in_at > timezone.now() + timedelta(minutes=5):
        raise MovementError("Check-in time can't be in the future.")
    if mv.vehicle_id:
        if odometer_in is None:
            raise MovementError("Enter the odometer reading on return.")
        if mv.odometer_out is not None and odometer_in < mv.odometer_out:
            raise MovementError(f"Odometer on return ({odometer_in:,} km) is below the reading at check-out "
                                f"({mv.odometer_out:,} km).")
    mv.checked_in_at, mv.checked_in_by = checked_in_at, user
    mv.odometer_in = odometer_in if mv.vehicle_id else None
    mv.fuel_in, mv.condition_in, mv.notes_in = fuel_in, condition_in or TripMovement.CONDITION_OK, notes_in
    mv.save()
    if mv.vehicle_id:
        v = Vehicle.objects.select_for_update().get(pk=mv.vehicle_id)
        v.status = RETURN_STATUS.get(mv.condition_in, Vehicle.AVAILABLE)
        v.odometer_km = max(v.odometer_km, odometer_in)
        v.save(update_fields=["status", "odometer_km", "updated_at", "updated_by"])
    back = [p.pk for p in mv.crew if not _busy(p, exclude_pk=mv.pk)]
    Employee.objects.filter(pk__in=back, on_duty=Employee.OUT_ON_TRIP).update(on_duty=Employee.IN_OFFICE,
                                                                              updated_at=timezone.now())
    if mv.late_alerted_at:
        from apps.notifications.models import Notification

        Notification.objects.filter(dedupe_key=f"late:{mv.pk}", read_at__isnull=True).update(read_at=timezone.now())
    return mv


def available_crew(role: str):
    """Drivers or guides who can be checked out right now."""
    busy = set(TripMovement.objects.open().values_list("driver", flat=True)) | \
        set(TripMovement.objects.open().values_list("guide", flat=True))
    qs = Employee.objects.filter(is_active=True, **{f"is_{role}": True}).exclude(pk__in=busy - {None})
    return [e for e in qs if e.is_available]


# ---------------------------------------------------------------------------------------------- capture queues
QUEUE_STATES = [("waiting", "Waiting for an agent"), ("assigned", "Assigned, not yet contacted"),
                ("contacted", "Contacted")]


def desk_enquiries():
    """Every lead from every channel lands at the Front Desk, which hands it to an agent (social and website leads included)."""
    return Enquiry.objects.select_related("client", "package", "destination", "assigned_agent")


def filter_state(qs, state: str):
    if state == "waiting":
        return qs.filter(assigned_agent__isnull=True).exclude(stage__in=["lost", "completed"])
    if state == "assigned":
        return qs.filter(assigned_agent__isnull=False, last_contact_at__isnull=True)
    if state == "contacted":
        return qs.filter(last_contact_at__isnull=False)
    return qs


def queue_state(enquiry: Enquiry) -> str:
    if enquiry.assigned_agent_id is None:
        return "waiting"
    return "contacted" if enquiry.last_contact_at else "assigned"


def day_bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------------------------------------- daily report
def desk_day_summary(day) -> dict:
    """Everything the reservations desk did on one day. Used by the report screen and the evening notification."""
    start, end = day_bounds(day)
    enq = desk_enquiries().filter(created_at__gte=start, created_at__lt=end).annotate(
        first_assigned=Min("assignments__assigned_at")).order_by("created_at")
    enquiries = list(enq)
    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    by_channel = []
    for code in DESK_SOURCES:
        rows = [e for e in enquiries if e.source == code]
        by_channel.append({"code": code, "label": labels[code], "count": len(rows),
                           "assigned": sum(1 for e in rows if e.assigned_agent_id),
                           "returning": sum(1 for e in rows if e.is_returning_client)})
    waits = [(e.first_assigned - e.created_at).total_seconds() for e in enquiries if e.first_assigned]
    by_agent = list(enq.filter(assigned_agent__isnull=False).values("assigned_agent__full_name")
                    .annotate(n=Count("pk")).order_by("-n", "assigned_agent__full_name"))
    mv = TripMovement.objects.select_related("vehicle", "driver", "guide", "destination")
    out_today = list(mv.filter(checked_out_at__gte=start, checked_out_at__lt=end))
    back_today = list(mv.filter(checked_in_at__gte=start, checked_in_at__lt=end))
    still_out = list(mv.filter(checked_out_at__lt=end).filter(Q(checked_in_at__isnull=True) | Q(checked_in_at__gte=end))
                     .order_by("expected_return_at"))
    late = [m for m in back_today if m.returned_late] + [m for m in still_out if m.expected_return_at < min(end, timezone.now())]
    return {
        "day": day,
        "enquiries": enquiries,
        "total": len(enquiries),
        "by_channel": by_channel,
        "assigned": sum(1 for e in enquiries if e.assigned_agent_id),
        "unassigned": sum(1 for e in enquiries if not e.assigned_agent_id),
        "returning": sum(1 for e in enquiries if e.is_returning_client),
        "avg_assign_seconds": int(sum(waits) / len(waits)) if waits else None,
        "by_agent": by_agent,
        "out_today": out_today,
        "back_today": back_today,
        "still_out": still_out,
        "late": late,
    }


# ---------------------------------------------------------------------------------------------- agents board, report
def agent_board():
    """Every active sales agent with today's load, open leads and status (used by the dashboard and the New Lead picker)."""
    from apps.leads.models import OPEN_STAGES
    from apps.leads.models import LeadAssignment

    today = timezone.localdate()
    load = dict(LeadAssignment.objects.filter(assigned_at__date=today).values_list("agent").annotate(n=Count("pk")))
    opened = dict(Enquiry.objects.filter(stage__in=OPEN_STAGES, assigned_agent__isnull=False).values_list("assigned_agent").annotate(n=Count("pk")))
    rows = []
    for a in Employee.objects.filter(is_sales_agent=True, is_active=True).prefetch_related("specialties").order_by("full_name"):
        n = load.get(a.pk, 0)
        rows.append({"agent": a, "today": n, "limit": a.daily_enquiry_limit, "open": opened.get(a.pk, 0), "available": a.is_available and n < a.daily_enquiry_limit,
                     "status": a.get_on_duty_display() if a.is_available else "Unavailable", "specialties": ", ".join(sp.name for sp in a.specialties.all())})
    return rows


def desk_period_report(period: str) -> dict:
    """Daily / weekly / monthly figures for the Front Desk Reports page, compared with the previous period."""
    from apps.leads.models import Source

    today = timezone.localdate()
    if period == "daily":
        start, end = today, today
        pstart = pend = today - timedelta(days=1)
    elif period == "weekly":
        start = today - timedelta(days=today.weekday()); end = start + timedelta(days=6)
        pstart, pend = start - timedelta(days=7), start - timedelta(days=1)
    else:
        start = today.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = nxt - timedelta(days=1)
        pend = start - timedelta(days=1); pstart = pend.replace(day=1)
    desk = desk_enquiries()
    cur = desk.filter(created_at__date__range=(start, end)).annotate(first_assigned=Min("assignments__assigned_at"))
    prev = desk.filter(created_at__date__range=(pstart, pend)).count()
    leads = list(cur)
    total = len(leads)
    contacted = sum(1 for e in leads if e.last_contact_at)
    waits = [(e.first_assigned - e.created_at).total_seconds() for e in leads if e.first_assigned]
    labels = __import__('apps.leads.models', fromlist=['x']).source_labels()
    by_source = [{"label": labels[c], "contacted": sum(1 for e in leads if e.source == c and e.last_contact_at),
                  "waiting": sum(1 for e in leads if e.source == c and not e.last_contact_at)} for c in DESK_SOURCES]
    weeks = []
    mon = today - timedelta(days=today.weekday())
    for i in range(7, -1, -1):
        ws = mon - timedelta(weeks=i)
        weeks.append({"label": f"{ws:%d %b}", "n": desk.filter(created_at__date__range=(ws, ws + timedelta(days=6))).count()})
    agents = []
    for r in agent_board():
        mine = [e for e in leads if e.assigned_agent_id == r["agent"].pk]
        done = sum(1 for e in mine if e.last_contact_at)
        agents.append({"name": r["agent"].full_name, "assigned": len(mine), "contacted": done, "waiting": len(mine) - done,
                       "rate": round(100 * done / len(mine)) if mine else 0})
    avg = int(sum(waits) / len(waits)) if waits else 0
    return {"period": period, "start": start, "end": end, "total": total, "delta_pct": round(100 * (total - prev) / prev) if prev else None,
            "contacted": contacted, "not_contacted": total - contacted, "contact_rate": round(100 * contacted / total) if total else 0, "avg_assign_minutes": round(avg / 60, 1),
            "by_source": by_source, "weeks": weeks, "agents": agents}
