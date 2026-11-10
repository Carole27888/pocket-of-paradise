import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.models import User
from apps.accounts.permissions import module_required
from apps.core.tables import Col, TableScreen

from . import services
from .models import AuditLog, IntegrationCredential, TodoItem, WeeklyPlanItem

CRED_ROLES = (R.ADMIN, R.COO, R.DIRECTOR, R.ICT_OFFICER)   # the CEO may manage keys despite being read-only elsewhere


def _need(user, *names):
    if not user.has_role(*names):
        raise PermissionDenied


def _ns(request):
    """"system:" or "ict:" : the same screens are reachable from Administration and from ICT, and stay in the menu they were opened from."""
    return (request.resolver_match.namespace or "system") + ":"


IMPORT_ROLES = (R.ADMIN, R.ICT_OFFICER)


# ================================================================================================ roles & access board
@module_required("admin")
def roles_board(request):
    _need(request.user, *R.EXECUTIVES)
    groups = {g.name: g for g in Group.objects.filter(name__in=R.ALL_ROLES)}
    cols = []
    for name in R.ALL_ROLES:
        g = groups.get(name)
        members = list(User.objects.filter(is_active=True, groups=g).order_by("full_name")) if g else []
        cols.append({"role": name, "desc": R.ROLE_DESCRIPTIONS.get(name, ""), "members": members,
                     "locked": name == R.DIRECTOR and not request.user.has_role(R.DIRECTOR)})
    without = User.objects.filter(is_active=True, groups__isnull=True, is_superuser=False)
    return render(request, "system/roles_board.html", {"cols": cols, "no_role": without,
                                                       "is_ceo": request.user.has_role(R.DIRECTOR)})


@require_POST
@module_required("admin")
def roles_move(request):
    """Move a person from one role column to another (JSON). Executives only; the COO can never create or touch a CEO."""
    _need(request.user, *R.EXECUTIVES)
    target = get_object_or_404(User, pk=request.POST.get("user") or 0, is_active=True)
    src, dst = request.POST.get("from", ""), request.POST.get("to", "")
    if dst not in R.ALL_ROLES or (src and src not in R.ALL_ROLES):
        return JsonResponse({"ok": False, "error": "Unknown role."}, status=400)
    is_ceo = request.user.has_role(R.DIRECTOR)
    if not is_ceo and (dst == R.DIRECTOR or src == R.DIRECTOR or target.has_role(R.DIRECTOR)):
        return JsonResponse({"ok": False, "error": "Only the CEO can change who holds the Director role."}, status=403)
    if target.pk == request.user.pk and not is_ceo and R.COO in (src, dst):
        return JsonResponse({"ok": False, "error": "You can't change your own role."}, status=403)
    if target.pk == request.user.pk and src == R.DIRECTOR:
        return JsonResponse({"ok": False, "error": "The CEO can't remove their own Director role (it would lock everyone out)."}, status=403)
    if src == dst:
        return JsonResponse({"ok": True, "unchanged": True})
    before = sorted(target.groups.values_list("name", flat=True))
    if src:
        target.groups.remove(Group.objects.get(name=src))
    target.groups.add(Group.objects.get_or_create(name=dst)[0])
    after = sorted(target.groups.values_list("name", flat=True))
    services.audit(request.user, "role.changed", target, f"{src or '(none)'} → {dst}. Roles before: {', '.join(before) or '—'}; after: {', '.join(after)}", request)
    from apps.notifications.services import notify

    notify(target, "Your role changed", f"You are now: {', '.join(after)}", "/", "info", dedupe_key=f"role:{target.pk}:{timezone.now():%Y%m%d%H%M}")
    return JsonResponse({"ok": True, "roles": after})


# ================================================================================================ system logs
class SystemLogs(TableScreen):
    module_key = "admin"
    model = AuditLog
    search_fields = ["action", "target_label", "detail", "user_label"]
    sort_fields = ["at", "action"]
    default_sort = "-at"
    title = "System Logs"
    subtitle = "Every audit-logged action: role changes, saved keys, data imports, anonymisation and more."
    placeholder = "Search action, person, record, detail…"
    export_name = "system-logs"
    paginate_by = 40
    columns = [
        Col("When", "at", sort="at", kind="datetime"),
        Col("Who", "user_label", sub="ip"),
        Col("Action", "action", sort="action", kind="pill", tone=lambda o: "danger" if o.action.endswith(("failed", "anonymised", "removed", "breach")) else "info"),
        Col("Record", "target_label", sub="target_type"),
        Col("Detail", "short_detail"),
    ]

    def dispatch(self, request, *a, **kw):
        if request.user.is_authenticated and not request.user.has_role(R.ADMIN, *R.EXECUTIVES):
            raise PermissionDenied
        return super().dispatch(request, *a, **kw)

    def kpis(self, qs):
        today = timezone.localdate()
        return [{"label": "Last 24 hours", "value": qs.filter(at__gte=timezone.now() - timedelta(days=1)).count(), "accent": True},
                {"label": "Role changes (30 days)", "value": qs.filter(action="role.changed", at__gte=timezone.now() - timedelta(days=30)).count()},
                {"label": "Key changes (30 days)", "value": qs.filter(action__startswith="credential", at__gte=timezone.now() - timedelta(days=30)).count()},
                {"label": "All time", "value": qs.count()}]


AuditLog.short_detail = property(lambda self: (self.detail[:90] + "…") if len(self.detail) > 90 else self.detail)


# ================================================================================================ integration keys
@module_required("admin", "ict")
def credentials(request):
    _need(request.user, *CRED_ROLES)
    rows = []
    for key, (label, fields, env, powers) in services.PROVIDERS.items():
        row = IntegrationCredential.objects.filter(provider=key).first()
        shown = []
        for name, flabel, secret in fields:
            val = services.get_credential(key, name)
            shown.append({"name": name, "label": flabel, "secret": secret, "has": bool(val),
                          "display": services.mask(val) if secret else val, "from_env": bool(val) and not (row and row.values.get(name))})
        rows.append({"key": key, "label": label, "powers": powers, "fields": shown, "row": row})
    return render(request, "system/credentials.html", {"rows": rows, "mode": __import__("django.conf", fromlist=["settings"]).settings.INTEGRATIONS_MODE})


@require_POST
@module_required("admin", "ict")
def credential_save(request, provider):
    _need(request.user, *CRED_ROLES)
    if provider not in services.PROVIDERS:
        raise PermissionDenied
    services.save_credentials(provider, request.POST, request.user, request)
    messages.success(request, f"{services.PROVIDERS[provider][0]} saved. It takes effect immediately.")
    return _back(request)


def _back(request):
    nxt = request.POST.get("next") or ""
    if nxt.startswith("/ict/") or nxt.startswith("/system/"):
        return redirect(nxt)
    return redirect("ict:settings" if _ns(request) == "ict:" else "system:credentials")


@require_POST
@module_required("admin", "ict")
def credential_test(request, provider):
    _need(request.user, *CRED_ROLES)
    if provider not in services.PROVIDERS:
        raise PermissionDenied
    ok, msg = services.test_credentials(provider, request.user, request)
    (messages.success if ok else messages.error)(request, f"{services.PROVIDERS[provider][0]}: {msg}")
    return _back(request)


@require_POST
@module_required("admin", "ict")
def credential_delete(request, provider):
    _need(request.user, *CRED_ROLES)
    services.delete_credentials(provider, request.user, request)
    messages.success(request, "Saved key removed (any environment variable still applies).")
    return _back(request)


# ================================================================================================ private to-do list
@module_required("me")
def todo(request):
    from . import planning

    items = TodoItem.objects.filter(user=request.user)   # strictly the owner's: no manager / CEO / admin override
    today = timezone.localdate()
    try:
        monday = WeeklyPlanItem.monday(timezone.datetime.fromisoformat(request.GET.get("week", "")).date())
    except ValueError:
        monday = WeeklyPlanItem.monday(today)
    days, summary = planning.board(request.user, monday)
    return render(request, "system/todo.html", {
        "open": [i for i in items if not i.done_at], "done": [i for i in items if i.done_at][:15], "today": today,
        "days": days, "summary": summary, "monday": monday, "sunday": monday + timedelta(days=6),
        "prev_week": monday - timedelta(days=7), "next_week": monday + timedelta(days=7),
        "this_week": monday == WeeklyPlanItem.monday(today), "nudge": planning.nudge(request.user)})


def _is_modal(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _plan_day(raw, fallback):
    try:
        return timezone.datetime.fromisoformat(raw).date()
    except (TypeError, ValueError):
        return fallback


def _plan_time(raw):
    try:
        return timezone.datetime.strptime(raw, "%H:%M").time() if raw else None
    except ValueError:
        return None


@module_required("me")
def plan_add(request):
    """Add to the week. Tick several days to plan the same thing on each of them."""
    from . import planning

    today = timezone.localdate()
    start = _plan_day(request.GET.get("day") or request.POST.get("day"), today)
    monday = WeeklyPlanItem.monday(start)
    error = ""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()[:200]
        days = sorted({d for d in (_plan_day(v, None) for v in request.POST.getlist("days")) if d})
        if not title:
            error = "Say what you will do."
        elif not days:
            error = "Pick at least one day."
        else:
            at = _plan_time(request.POST.get("start_time"))
            for d in days:
                WeeklyPlanItem.objects.create(user=request.user, day=d, title=title, start_time=at)
            messages.success(request, f"Added to {len(days)} day{'s' if len(days) != 1 else ''}.")
            return JsonResponse({"ok": True}) if _is_modal(request) else redirect("system:todo")
        if not _is_modal(request):
            messages.error(request, error)
    ctx = {"week": planning.week_days(monday), "start": start, "error": error, "posted": request.POST, "ticked": request.POST.getlist("days") or [start.isoformat()]}
    if _is_modal(request):
        return render(request, "system/plan_modal.html", ctx, status=422 if error else 200)
    return render(request, "system/plan_page.html", ctx)


def _own_plan(request, pk):
    return get_object_or_404(WeeklyPlanItem, pk=pk, user=request.user)


@module_required("me")
def plan_edit(request, pk):
    item = _own_plan(request, pk)
    error = ""
    if request.method == "POST":
        title = request.POST.get("title", "").strip()[:200]
        if not title:
            error = "Say what you will do."
        else:
            item.title = title
            item.day = _plan_day(request.POST.get("day"), item.day)
            item.start_time = _plan_time(request.POST.get("start_time"))
            item.save()
            messages.success(request, "Updated.")
            return JsonResponse({"ok": True}) if _is_modal(request) else redirect("system:todo")
    ctx = {"item": item, "error": error, "edit": True}
    return render(request, "system/plan_item_modal.html", ctx, status=422 if error else 200)


@module_required("me")
def plan_mark(request, pk):
    """Done is one click. Partly done and not done ask how far you got, in a pop-up."""
    item = _own_plan(request, pk)
    status = request.POST.get("status") or request.GET.get("status") or "done"
    if status not in (WeeklyPlanItem.DONE, WeeklyPlanItem.PARTIAL, WeeklyPlanItem.MISSED, WeeklyPlanItem.PLANNED):
        status = "done"
    if item.day > timezone.localdate():
        raise PermissionDenied   # you cannot mark tomorrow's work as done today
    if request.method == "POST":
        item.status = status
        item.note = request.POST.get("note", "").strip()[:300]
        item.marked_at = timezone.now() if status != WeeklyPlanItem.PLANNED else None
        item.save(update_fields=["status", "note", "marked_at"])
        return JsonResponse({"ok": True}) if _is_modal(request) else redirect("system:todo")
    return render(request, "system/plan_item_modal.html", {"item": item, "mark": status, "error": ""})


@require_POST
@module_required("me")
def plan_delete(request, pk):
    _own_plan(request, pk).delete()
    return redirect("system:todo")


@module_required("me")
def team_plans(request):
    """How everyone is doing with their weekly plan: who has planned, and how much they got done."""
    from . import planning

    if not (request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO, R.DIRECTOR, R.SALES_MANAGER)):
        raise PermissionDenied
    today = timezone.localdate()
    try:
        monday = WeeklyPlanItem.monday(timezone.datetime.fromisoformat(request.GET.get("week", "")).date())
    except ValueError:
        monday = WeeklyPlanItem.monday(today)
    users = User.objects.filter(is_active=True).exclude(employee__isnull=True).select_related("employee").order_by("full_name")
    items = list(WeeklyPlanItem.objects.filter(day__range=(monday, monday + timedelta(days=6))))
    by_user = {}
    for i in items:
        by_user.setdefault(i.user_id, []).append(i)
    rows = []
    for u in users:
        mine = by_user.get(u.pk, [])
        rows.append({"user": u, "s": planning.week_summary(mine), "planned": bool(mine)})
    rows.sort(key=lambda r: (r["planned"], -(r["s"]["pct"] or 0)))
    return render(request, "system/team_plans.html", {"rows": rows, "monday": monday, "sunday": monday + timedelta(days=6),
                                                      "prev_week": monday - timedelta(days=7), "next_week": monday + timedelta(days=7),
                                                      "not_planned": sum(1 for r in rows if not r["planned"])})


@require_POST
@module_required("me")
def todo_add(request):
    text = request.POST.get("text", "").strip()
    if text:
        due = request.POST.get("due") or None
        try:
            due = timezone.datetime.fromisoformat(due).date() if due else None
        except ValueError:
            due = None
        TodoItem.objects.create(user=request.user, text=text[:240], due=due)
    return redirect("system:todo")


@require_POST
@module_required("me")
def todo_action(request, pk):
    item = get_object_or_404(TodoItem, pk=pk, user=request.user)
    a = request.POST.get("action")
    if a == "done":
        item.done_at = timezone.now()
        item.save(update_fields=["done_at"])
    elif a == "undo":
        item.done_at = None
        item.save(update_fields=["done_at"])
    elif a == "remove":
        item.delete()
    return redirect("system:todo")


# ================================================================================================ bulk import
@module_required("admin", "ict")
def imports_index(request):
    from . import imports

    _need(request.user, *IMPORT_ROLES)
    if request.method == "POST":
        kind, f = request.POST.get("kind", ""), request.FILES.get("file")
        if kind not in imports.IMPORTERS or f is None:
            messages.error(request, "Choose a data type and a file.")
        else:
            try:
                batch = imports.check(kind, f.name, f.read(), request.user)
                return redirect(_ns(request) + "import_batch", pk=batch.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
    from .models import ImportBatch

    return render(request, "system/imports.html", {"ns": _ns(request), "importers": imports.IMPORTERS.values(), "batches": ImportBatch.objects.all()[:15]})


@module_required("admin", "ict")
def import_template(request, kind):
    from . import imports
    from django.http import HttpResponse

    _need(request.user, *IMPORT_ROLES)
    imp = imports.IMPORTERS.get(kind)
    if imp is None:
        raise PermissionDenied
    resp = HttpResponse(imp.template_csv(), content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{kind}-template.csv"'
    return resp


@module_required("admin", "ict")
def import_batch(request, pk):
    from . import imports
    from .models import ImportBatch

    _need(request.user, *IMPORT_ROLES)
    b = get_object_or_404(ImportBatch, pk=pk)
    return render(request, "system/import_batch.html", {
        "ns": _ns(request), "b": b, "imp": imports.IMPORTERS[b.kind], "bad": [r for r in b.rows if r["errors"]][:100],
        "preview": [r for r in b.rows if not r["errors"]][:8], "undo_days": ImportBatch.UNDO_DAYS})


@require_POST
@module_required("admin", "ict")
def import_action(request, pk):
    from . import imports
    from .models import ImportBatch

    _need(request.user, *IMPORT_ROLES)
    b = get_object_or_404(ImportBatch, pk=pk)
    a = request.POST.get("action")
    try:
        if a == "commit":
            imports.commit(b, request.user)
            messages.success(request, f"Imported: {b.created} created, {b.updated} updated, {b.skipped} unchanged" + (f", {b.failed} failed." if b.failed else "."))
        elif a == "undo":
            imports.undo(b, request.user)
            messages.success(request, "Import undone: the records it created were removed.")
        elif a == "discard" and b.status == ImportBatch.CHECKED:
            b.status = ImportBatch.DISCARDED
            b.save(update_fields=["status"])
            messages.success(request, "Batch discarded. Nothing was imported.")
            return redirect(_ns(request) + "imports")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(_ns(request) + "import_batch", pk=pk)
