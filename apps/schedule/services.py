"""Calendar rules: rank-based invites, owner-only edits, and events other modules create (leave)."""
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts import roles as R

from .models import CalendarEvent, EventInvite

EXEC, MANAGER, STAFF = 3, 2, 1
MANAGER_ROLES = {R.SALES_MANAGER, R.FRONTDESK_MANAGER, R.OPERATIONS, R.FINANCE, R.MARKETING, R.PRODUCTION_LEAD, R.SUPPLIER_COORDINATOR}
DATETIME_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S")


def calendar_rank(user) -> int:
    """Executive (CEO / COO / Admin) outranks manager outranks staff."""
    if user.is_superuser or user.has_role(*R.ALL_ACCESS):
        return EXEC
    return MANAGER if user.has_role(*MANAGER_ROLES) else STAFF


def parse_local(value: str):
    """Parse the browser's datetime-local value (always ISO, whatever the user's locale) into an aware datetime."""
    value = (value or "").strip()
    for fmt in DATETIME_FORMATS:
        try:
            return timezone.make_aware(datetime.strptime(value, fmt))
        except ValueError:
            continue
    raise ValueError("Enter a valid date and time.")


@transaction.atomic
def create_event(owner, *, title, start, end=None, all_day=False, department="all", kind="meeting", location="", notes="", source="") -> CalendarEvent:
    if end and end < start:
        raise ValueError("The event can't end before it starts.")
    return CalendarEvent.objects.create(title=title.strip()[:160], start=start, end=end, all_day=all_day, department=department, kind=kind,
                                        owner=owner, location=location[:120], notes=notes, source=source, created_by=owner)


def invite(event: CalendarEvent, inviter, invitee) -> EventInvite:
    """Inviting someone who outranks you needs their acceptance; equal or lower rank goes straight onto their calendar."""
    if invitee.pk == event.owner_id:
        raise ValueError("The organiser is already on the event.")
    status = EventInvite.PENDING if calendar_rank(invitee) > calendar_rank(inviter) else EventInvite.ACCEPTED
    obj, created = EventInvite.objects.get_or_create(event=event, user=invitee, defaults={"status": status, "invited_by": inviter,
                                                     "responded_at": timezone.now() if status == EventInvite.ACCEPTED else None})
    if created and status == EventInvite.PENDING:
        from apps.notifications.services import notify

        notify(invitee, f"Invitation: {event.title}", f"{inviter.full_name} · {timezone.localtime(event.start):%a %d %b, %I:%M %p}",
               "/schedule/", "info", dedupe_key=f"invite:{obj.pk}")
    return obj


def respond(invite_obj: EventInvite, user, accept: bool) -> EventInvite:
    if invite_obj.user_id != user.pk:
        raise PermissionError("Not your invitation.")
    invite_obj.status = EventInvite.ACCEPTED if accept else EventInvite.DECLINED
    invite_obj.responded_at = timezone.now()
    invite_obj.save(update_fields=["status", "responded_at"])
    return invite_obj


def book_ceo(booker, ceo, *, title, start, end=None, notes=""):
    """The CEO has nobody above them to approve an invite, so Front Desk staff may book straight onto the CEO's calendar."""
    if not booker.has_role(R.FRONTDESK_MANAGER, R.RESERVATIONS, R.RECEPTIONIST, R.COO, R.ADMIN):
        raise PermissionError("Only the Front Desk, COO and Admin can book the CEO's calendar directly.")
    ev = create_event(booker, title=title, start=start, end=end, department="executive", kind=CalendarEvent.BOOKING, notes=notes)
    EventInvite.objects.create(event=ev, user=ceo, status=EventInvite.ACCEPTED, invited_by=booker, responded_at=timezone.now())
    return ev


def visible_events(user, start, end, department=""):
    """Events on a user's calendar within [start, end]: theirs, ones they accepted, and (department view) that department's."""
    window = Q(start__gte=start, start__lt=end) | Q(end__gte=start, start__lt=end)
    mine = Q(owner=user) | Q(invites__user=user, invites__status=EventInvite.ACCEPTED)
    if department and department != "mine":
        dept = Q(department=department) | Q(department="all")
        if not (user.is_superuser or user.has_role(*R.ALL_ACCESS)):
            dept &= (Q(owner=user) | Q(invites__user=user, invites__status=EventInvite.ACCEPTED) | Q(department__in=["all", department]))
        return CalendarEvent.objects.filter(window & dept).distinct()
    return CalendarEvent.objects.filter(window & (mine | Q(department="all"))).distinct()


def sync_leave_event(leave_request):
    """Approved leave puts an all-day event on the person's calendar; any other status removes it."""
    src = f"leave:{leave_request.pk}"
    CalendarEvent.objects.filter(source=src).delete()
    if leave_request.status != "approved":
        return None
    u = leave_request.employee.user
    start = timezone.make_aware(datetime.combine(leave_request.start_date, datetime.min.time()))
    end = timezone.make_aware(datetime.combine(leave_request.end_date, datetime.max.time().replace(microsecond=0)))
    return CalendarEvent.objects.create(title=f"{leave_request.employee.full_name}: {leave_request.leave_type.name}", start=start, end=end, all_day=True,
                                        department="hr", kind=CalendarEvent.LEAVE, owner=u, source=src)
