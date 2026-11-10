from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.notifications.services import notify

from .models import Task

ESCALATE_AGAIN_AFTER = timedelta(hours=24)


class TaskError(Exception):
    pass


def executives() -> list:
    return list(get_user_model().objects.filter(is_active=True, groups__name__in=[R.COO, R.DIRECTOR, R.ADMIN]).distinct())


def managers_of(employee) -> list:
    if employee.manager_id and employee.manager.user_id:
        return [employee.manager.user]
    return executives()


def create(*, title, owner, user, due_at, details="", kind=Task.Kind.TASK, priority=Task.Priority.NORMAL, enquiry=None) -> Task:
    if not (title or "").strip():
        raise TaskError("Give it a title.")
    task = Task.objects.create(title=title.strip()[:200], details=details, owner=owner, created_by=user, due_at=due_at, kind=kind, priority=priority, enquiry=enquiry)
    if owner.user_id and owner.user_id != getattr(user, "pk", None):
        notify(owner.user, f"New {task.get_kind_display().lower()}: {task.title}", f"Due {timezone.localtime(due_at):%a %d %b %H:%M}", url=reverse("tasks:list"),
               level="warning" if kind == Task.Kind.COMPLAINT else "info")
    if enquiry is not None and kind == Task.Kind.COMPLAINT:
        from apps.leads.services import log_activity

        log_activity(enquiry, "note", f"Complaint logged: {task.title}", user)
    if kind == Task.Kind.COMPLAINT:  # managers should know the day a complaint is raised, not when it is late
        for u in managers_of(owner):
            if u.pk not in (owner.user_id, getattr(user, "pk", None)):
                notify(u, f"Complaint: {task.title}", f"Given to {owner.full_name}", url=reverse("tasks:list") + "?view=team", level="warning")
    return task


def complete(task: Task, user, note="") -> Task:
    if task.status != Task.Status.OPEN:
        raise TaskError("This one is already closed.")
    task.status, task.done_at, task.done_note = Task.Status.DONE, timezone.now(), (note or "").strip()
    task.save(update_fields=["status", "done_at", "done_note", "updated_at"])
    if task.enquiry_id:
        from apps.leads.services import log_activity

        log_activity(task.enquiry, "note", f"{task.get_kind_display()} resolved: {task.title}" + (f". {task.done_note}" if task.done_note else ""), user)
    if task.created_by_id and task.created_by_id != getattr(user, "pk", None):
        notify(task.created_by, f"Done: {task.title}", task.done_note or f"Completed by {user}", url=reverse("tasks:list"))
    return task


def escalate_overdue(now=None) -> int:
    """Owner is reminded on every run until it is done; the manager hears once; a day later the executives hear once."""
    now = now or timezone.now()
    n = 0
    for t in Task.objects.filter(status=Task.Status.OPEN, due_at__lt=now).select_related("owner__manager__user"):
        hours = int((now - t.due_at).total_seconds() // 3600)
        if t.owner.user_id:
            notify(t.owner.user, f"Still open: {t.title}", f"{hours} h overdue. Please finish it or update it.", url=reverse("tasks:list"), dedupe_key=f"task-remind:{t.pk}:{now:%Y%m%d%H}")
        if t.escalation_level == 0:
            targets, level = managers_of(t.owner), 1
        elif t.escalation_level == 1 and t.escalated_at and now - t.escalated_at >= ESCALATE_AGAIN_AFTER:
            targets, level = executives(), 2
        else:
            continue
        for u in targets:
            if u.pk != t.owner.user_id:
                notify(u, f"Overdue {t.get_kind_display().lower()}: {t.title}", f"{t.owner.full_name} · {hours} h overdue" + (" · escalated again" if level == 2 else ""),
                       url=reverse("tasks:list") + "?view=team", level="danger")
        t.escalation_level, t.escalated_at = level, now
        t.save(update_fields=["escalation_level", "escalated_at", "updated_at"])
        n += 1
    return n


def is_manager(user) -> bool:
    e = getattr(user, "employee", None)
    return bool(user.is_superuser or user.has_role(*R.ALL_ACCESS, R.SALES_MANAGER, R.FRONTDESK_MANAGER) or (e and e.reports.exists()))


def visible(user):
    qs = Task.objects.select_related("owner", "created_by", "enquiry__client")
    if user.is_superuser or user.has_role(*R.ALL_ACCESS):
        return qs
    e = getattr(user, "employee", None)
    q = Q(created_by=user)
    if e:
        q |= Q(owner=e) | Q(owner__manager=e)
    if user.has_role(R.SALES_MANAGER, R.FRONTDESK_MANAGER) and e and e.department_id:
        q |= Q(owner__department_id=e.department_id)
    return qs.filter(q)
