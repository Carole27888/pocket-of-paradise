from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required

from . import services
from .forms import TaskForm
from .models import Task


@module_required("me")
def task_list(request):
    view = request.GET.get("view", "mine")
    emp = getattr(request.user, "employee", None)
    qs = services.visible(request.user)
    if view == "mine":
        qs = qs.filter(owner=emp) if emp else qs.none()
    elif view == "given":
        qs = qs.filter(created_by=request.user).exclude(owner=emp) if emp else qs.filter(created_by=request.user)
    elif view == "complaints":
        qs = qs.filter(kind=Task.Kind.COMPLAINT)
    closed = request.GET.get("closed") == "1"
    if not closed:
        qs = qs.filter(status=Task.Status.OPEN)
    tabs = [("mine", "My tasks"), ("given", "Given to others"), ("complaints", "Complaints")]
    if services.is_manager(request.user):
        tabs.insert(2, ("team", "Team"))
    return render(request, "tasks/list.html", {"tasks": qs[:200], "view": view, "tabs": tabs, "closed": closed, "now": timezone.now(), "emp": emp,
                                               "can_assign": services.is_manager(request.user)})


@module_required("me", "sales")
def task_new(request):
    from apps.leads.views import visible_enquiries

    kind = request.GET.get("kind") or request.POST.get("kind") or Task.Kind.TASK
    if kind not in Task.Kind.values:
        kind = Task.Kind.TASK
    enq = None
    raw = request.GET.get("enquiry") or request.POST.get("enquiry") or ""
    if raw.isdigit():
        enq = visible_enquiries(request.user).select_related("client", "assigned_agent").filter(pk=int(raw)).first()
    now = timezone.localtime()
    initial = {"due_at": (now + timedelta(days=1)).replace(minute=0, second=0, microsecond=0), "owner": getattr(request.user, "employee", None)}
    if kind == Task.Kind.COMPLAINT:
        initial["priority"] = Task.Priority.HIGH
        initial["due_at"] = (now + timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
        if enq:
            initial.update({"title": f"Complaint from {enq.client.full_name}", "owner": enq.assigned_agent or initial["owner"]})
    # complaints can go to anyone; everyday tasks go to others only if you manage people
    any_owner = services.is_manager(request.user) or kind == Task.Kind.COMPLAINT
    form = TaskForm(request.POST or None, initial=initial, user=request.user, any_owner=any_owner)
    modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        services.create(title=d["title"], owner=d["owner"], user=request.user, due_at=d["due_at"], details=d["details"], kind=kind, priority=d["priority"], enquiry=enq)
        messages.success(request, f"{dict(Task.Kind.choices)[kind]} given to {d['owner'].full_name}.")
        if modal:
            return JsonResponse({"ok": True})
        return redirect(request.POST.get("next") or reverse("tasks:list"))
    ctx = {"form": form, "kind": kind, "enq": enq, "title": "Log a complaint" if kind == Task.Kind.COMPLAINT else "New task"}
    if modal:
        return render(request, "tasks/form_modal.html", ctx, status=422 if request.method == "POST" else 200)
    return render(request, "components/form_page.html", {"form": form, "title": ctx["title"], "cancel_url": reverse("tasks:list")})


@require_POST
@module_required("me", "sales")
def task_done(request, pk):
    t = get_object_or_404(services.visible(request.user), pk=pk)
    emp = getattr(request.user, "employee", None)
    if not (t.owner == emp or t.created_by_id == request.user.pk or services.is_manager(request.user)):
        raise PermissionDenied
    try:
        services.complete(t, request.user, request.POST.get("note", ""))
        messages.success(request, f"Done: {t.title}")
    except services.TaskError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or reverse("tasks:list"))
