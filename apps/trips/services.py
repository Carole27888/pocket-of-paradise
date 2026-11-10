"""Departure readiness, resource assignment, manifests and check-in."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.fleet.models import Vehicle
from apps.people.models import Employee

from .models import CHECKLIST, Departure, DepartureEvent, ManifestEntry, Stage

# Other modules register callbacks here (Marketing: review requests + content; Finance: revenue recognition).
COMPLETION_HOOKS: list = []
DEPARTURE_HOOKS: list = []


def log_event(dep: Departure, kind: str, text: str, user=None):
    return DepartureEvent.objects.create(departure=dep, kind=kind, text=text, user=user)


def _touch_stage(dep: Departure, stage: str):
    dep.stage = stage
    dep.stage_changed_at = timezone.now()


# ------------------------------------------------------------------------------------------------ resources
def _overlapping(dep: Departure, **field):
    return (Departure.objects.exclude(pk=dep.pk).exclude(stage__in=[Stage.COMPLETED, Stage.CANCELLED])
            .filter(start_date__lte=dep.end_date, end_date__gte=dep.start_date, **field))


def check_assignable(dep: Departure, *, guide=None, driver=None, vehicle=None):
    """Raise ValueError when a person/vehicle can't take the departure (off duty, in service, double-booked)."""
    for role, person in (("Guide", guide), ("Driver", driver)):
        if person is None:
            continue
        if not person.is_available:
            raise ValueError(f"{role} {person} is {person.get_on_duty_display().lower()}.")
        clash = _overlapping(dep).filter(Q(guide=person) | Q(driver=person)).first()
        if clash:
            raise ValueError(f"{role} {person} is already on {clash.reference} ({clash.start_date:%d %b}).")
    if vehicle is not None:
        if vehicle.status in (Vehicle.MAINTENANCE, Vehicle.OUT_OF_SERVICE):
            raise ValueError(f"{vehicle} is {vehicle.get_status_display().lower()}.")
        clash = _overlapping(dep, vehicle=vehicle).first()
        if clash:
            raise ValueError(f"{vehicle} is already on {clash.reference} ({clash.start_date:%d %b}).")
        if vehicle.seats < dep.seats_sold:
            raise ValueError(f"{vehicle} seats {vehicle.seats} but {dep.seats_sold} guests are booked.")


@transaction.atomic
def assign_resources(dep: Departure, user=None, *, guide=None, driver=None, vehicle=None) -> Departure:
    if dep.stage in (Stage.DEPARTED, Stage.COMPLETED, Stage.CANCELLED):
        raise ValueError("Resources can't be changed after departure.")
    check_assignable(dep, guide=guide if guide and guide.pk != dep.guide_id else None,
                     driver=driver if driver and driver.pk != dep.driver_id else None,
                     vehicle=vehicle if vehicle and vehicle.pk != dep.vehicle_id else None)
    changes = []
    for name, obj in (("guide", guide), ("driver", driver), ("vehicle", vehicle)):
        if obj is not None and getattr(dep, f"{name}_id") != obj.pk:
            setattr(dep, name, obj)
            changes.append(f"{name} → {obj}")
    if changes:
        dep.save()
        log_event(dep, "resources", "Assigned " + ", ".join(changes), user)
    return dep


@transaction.atomic
def set_checklist(dep: Departure, user=None, **flags) -> Departure:
    changed = []
    for field, label in CHECKLIST:
        if field in flags and bool(flags[field]) != getattr(dep, field):
            setattr(dep, field, bool(flags[field]))
            changed.append(f"{label}: {'yes' if flags[field] else 'no'}")
    if changed:
        dep.save()
        log_event(dep, "checklist", "; ".join(changed), user)
    return dep


# ------------------------------------------------------------------------------------------------ stages
@transaction.atomic
def mark_ready(dep: Departure, user=None) -> Departure:
    if dep.stage != Stage.PLANNED:
        raise ValueError("Only a planned departure can be marked ready.")
    missing = dep.missing_resources()
    if missing:
        raise ValueError("Still missing: " + ", ".join(missing) + ".")
    _touch_stage(dep, Stage.READY)
    dep.resources_confirmed_at, dep.resources_confirmed_by = timezone.now(), user
    dep.save()
    log_event(dep, "ready", "All resources confirmed", user)
    return dep


@transaction.atomic
def mark_departed(dep: Departure, user=None) -> Departure:
    if dep.stage != Stage.READY:
        raise ValueError("Mark the resources ready before departing.")
    if not (dep.guide_id and dep.vehicle_id):
        raise ValueError("A guide and a vehicle must be assigned.")
    if not dep.manifest.exists():
        raise ValueError("The manifest is empty. Add guests or generate it from the bookings.")
    _touch_stage(dep, Stage.DEPARTED)
    dep.departed_at, dep.departed_by = timezone.now(), user
    dep.save()
    Vehicle.objects.filter(pk=dep.vehicle_id).update(status=Vehicle.OUT)
    for person in (dep.guide, dep.driver):
        if person and person.on_duty == Employee.IN_OFFICE:
            Employee.objects.filter(pk=person.pk).update(on_duty=Employee.OUT_ON_TRIP)
    log_event(dep, "departed", f"Departed with {dep.manifest.count()} guests", user)
    for hook in DEPARTURE_HOOKS:
        hook(dep, user)
    return dep


@transaction.atomic
def mark_completed(dep: Departure, user=None) -> Departure:
    if dep.stage != Stage.DEPARTED:
        raise ValueError("Only a departed trip can be completed.")
    if dep.incidents.filter(severity="high", resolved_at__isnull=True).exists():
        raise ValueError("Resolve the open high-severity incident first.")
    _touch_stage(dep, Stage.COMPLETED)
    dep.completed_at, dep.completed_by = timezone.now(), user
    dep.save()
    if dep.vehicle_id:
        Vehicle.objects.filter(pk=dep.vehicle_id, status=Vehicle.OUT).update(status=Vehicle.AVAILABLE)
    Employee.objects.filter(pk__in=[p.pk for p in (dep.guide, dep.driver) if p],
                            on_duty=Employee.OUT_ON_TRIP).update(on_duty=Employee.IN_OFFICE)
    from apps.sales.models import BookingStatus
    from apps.sales.services import set_booking_status

    for booking in dep.bookings.filter(status=BookingStatus.CONFIRMED):
        set_booking_status(booking, BookingStatus.COMPLETED, user)
    log_event(dep, "completed", "Trip completed", user)
    for hook in COMPLETION_HOOKS:
        hook(dep, user)
    return dep


@transaction.atomic
def cancel(dep: Departure, user=None, reason="") -> Departure:
    if dep.stage in (Stage.DEPARTED, Stage.COMPLETED, Stage.CANCELLED):
        raise ValueError("This departure can't be cancelled.")
    if not reason:
        raise ValueError("Give a reason.")
    _touch_stage(dep, Stage.CANCELLED)
    dep.delay_reason = reason
    dep.save()
    dep.bookings.update(departure=None)
    log_event(dep, "cancelled", f"Cancelled: {reason}", user)
    return dep


def set_delay(dep: Departure, reason: str, user=None):
    dep.delay_reason = reason
    dep.save(update_fields=["delay_reason", "updated_at", "updated_by"])
    log_event(dep, "delay", f"Delay noted: {reason}" if reason else "Delay cleared", user)


# ------------------------------------------------------------------------------------------------ bookings
@transaction.atomic
def attach_booking(booking, dep: Departure, user=None):
    if dep.stage in (Stage.DEPARTED, Stage.COMPLETED, Stage.CANCELLED):
        raise ValueError("That departure is closed to new bookings.")
    pax = booking.adults + booking.children
    already = booking.departure_id == dep.pk
    if not already and pax > dep.seats_left:
        raise ValueError(f"Only {dep.seats_left} seat(s) left on {dep.reference}, the booking needs {pax}.")
    booking.departure = dep
    booking.travel_start, booking.travel_end = dep.start_date, dep.end_date
    booking.save(update_fields=["departure", "travel_start", "travel_end", "updated_at", "updated_by"])
    log_event(dep, "booking", f"{booking.reference} added ({pax} guests)", user)
    sync_manifest(dep)
    return booking


@transaction.atomic
def sync_manifest(dep: Departure) -> int:
    """Create manifest rows for every live booking that has none (never deletes edited rows)."""
    created = 0
    for b in dep.live_bookings.select_related("client", "enquiry"):
        if dep.manifest.filter(booking=b).exists():
            continue
        c = b.client
        ManifestEntry.objects.create(departure=dep, booking=b, full_name=c.full_name, nationality=c.nationality,
                                     passport_number=c.passport_number or "", phone=c.phone,
                                     dietary_notes=c.dietary_notes, medical_notes=c.medical_notes, is_lead=True,
                                     pickup_point=dep.meeting_point)
        created += 1
        members = list(b.enquiry.party_members.all())
        for m in members:
            ManifestEntry.objects.create(departure=dep, booking=b, full_name=m.full_name, is_child=m.is_child,
                                         age=m.age, passport_number=m.passport_number or "",
                                         dietary_notes=m.dietary_notes, nationality=c.nationality,
                                         pickup_point=dep.meeting_point)
            created += 1
        for i in range(max(0, b.adults + b.children - 1 - len(members))):  # placeholders to complete later
            ManifestEntry.objects.create(departure=dep, booking=b, full_name=f"{c.full_name.split()[-1]} guest {i + 2}",
                                         nationality=c.nationality, pickup_point=dep.meeting_point)
            created += 1
    return created


def check_in(entry: ManifestEntry, user=None, undo=False) -> ManifestEntry:
    entry.checked_in_at = None if undo else (entry.checked_in_at or timezone.now())
    entry.checked_in_by = None if undo else user
    entry.save(update_fields=["checked_in_at", "checked_in_by"])
    return entry


def check_in_all(dep: Departure, user=None) -> int:
    return dep.manifest.filter(checked_in_at__isnull=True).update(checked_in_at=timezone.now(), checked_in_by=user)


# ------------------------------------------------------------------------------------------------ incidents
@transaction.atomic
def report_incident(dep: Departure, user, *, kind, severity, description, action_taken="", guide=None):
    inc = dep.incidents.create(kind=kind, severity=severity, description=description, action_taken=action_taken,
                               guide=guide or dep.guide)
    log_event(dep, "incident", f"Incident ({inc.get_severity_display().lower()}): {inc.get_kind_display()}", user)
    if severity == "high":
        from apps.notifications.services import notify_roles

        from apps.accounts import roles

        notify_roles([roles.OPERATIONS, roles.ADMIN, roles.FRONTDESK_MANAGER],
                     f"High-severity incident on {dep.reference}", description[:200], inc.get_absolute_url(),
                     level="danger", dedupe_key=f"incident:{inc.pk}")
    return inc


def resolve_incident(inc, user=None, note=""):
    inc.resolved_at = timezone.now()
    if note:
        inc.action_taken = (inc.action_taken + "\n" + note).strip()
    inc.save(update_fields=["resolved_at", "action_taken", "updated_at", "updated_by"])
    log_event(inc.departure, "incident", f"Incident resolved: {inc.get_kind_display()}", user)
    return inc
