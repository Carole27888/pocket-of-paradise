import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification, PushSubscription


@login_required
def dropdown(request):
    items = Notification.objects.filter(user=request.user)[:12]
    return render(request, "notifications/dropdown.html", {"items": items})


@login_required
def live(request):
    """For the pop-up: alerts newer than `after` (the newest id the page has seen) and the unread count. `after` missing = just the baseline."""
    qs = Notification.objects.filter(user=request.user)
    newest = qs.values_list("pk", flat=True).first() or 0
    unread = qs.filter(read_at__isnull=True).count()
    after = request.GET.get("after", "")
    items = []
    if after.isdigit():
        items = [{"id": n.pk, "title": n.title, "body": n.body, "level": n.level, "url": f"/notifications/{n.pk}/open/"}
                 for n in qs.filter(pk__gt=int(after), read_at__isnull=True).order_by("pk")[:3]]
    resp = JsonResponse({"newest": newest, "unread": unread, "items": items})
    resp["Cache-Control"] = "no-store"
    return resp


@login_required
def open_notification(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return redirect(n.url or "core:home")


@require_POST
@login_required
def mark_all_read(request):
    Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
    resp = HttpResponse(status=204)
    resp["HX-Refresh"] = "true"
    return resp


@require_POST
@login_required
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        keys = data["keys"]
        PushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={"user": request.user, "p256dh": keys["p256dh"], "auth": keys["auth"],
                      "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]},
        )
    except (KeyError, ValueError):
        return HttpResponseBadRequest("invalid subscription")
    return JsonResponse({"ok": True})


@require_POST
@login_required
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body).get("endpoint", "")
    except ValueError:
        endpoint = ""
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"ok": True})


@login_required
def page(request):
    """The full notifications list (the bell shows only the latest dozen)."""
    from apps.accounts.permissions import module_required

    @module_required("me")
    def inner(req):
        show = req.GET.get("show", "all")
        qs = Notification.objects.filter(user=req.user)
        if show == "unread":
            qs = qs.filter(read_at__isnull=True)
        return render(req, "notifications/list.html", {"items": qs[:200], "show": show,
                                                       "unread": Notification.objects.filter(user=req.user, read_at__isnull=True).count()})

    return inner(request)


# ------------------------------------------------------------------------------------------------ notice board
def _can_post(user) -> bool:
    return user.is_superuser or user.has_role("Admin", "COO")


def _is_modal(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required
def board(request):
    from datetime import timedelta

    from apps.accounts.roles import ALL_ROLES

    from .models import announcements_for

    today = timezone.localdate()
    items = announcements_for(request.user)
    fresh = timezone.now() - timedelta(days=3)
    for a in items:
        a.is_new = a.created_at >= fresh
        a.days_left = (a.expires_on - today).days if a.expires_on else None
    month_start = today.replace(day=1)
    stats = {"active": len(items), "important": sum(1 for a in items if a.level == "important"),
             "new": sum(1 for a in items if a.is_new), "ending": sum(1 for a in items if a.days_left is not None and a.days_left <= 7)}
    # who is away today and who goes away in the next fortnight, from approved leave
    from apps.hr.models import LeaveRequest

    approved = LeaveRequest.objects.filter(status=LeaveRequest.APPROVED, end_date__gte=today, start_date__lte=today + timedelta(days=14)).select_related("employee", "leave_type")
    away = [r for r in approved if r.start_date <= today]
    soon = sorted((r for r in approved if r.start_date > today), key=lambda r: r.start_date)[:6]
    return render(request, "notifications/board.html", {"items": items, "stats": stats, "away": away, "soon": soon, "can_post": _can_post(request.user),
                                                        "roles": ALL_ROLES, "month_posts": len([a for a in items if a.created_at.date() >= month_start])})


@login_required
def board_form(request, pk=None):
    """Post or edit a notice in a pop-up. Posting notifies the people who can see it."""
    from datetime import date

    from django.core.exceptions import PermissionDenied
    from django.contrib import messages

    from apps.accounts.roles import ALL_ROLES

    from .models import Announcement

    if not _can_post(request.user):
        raise PermissionDenied
    a = get_object_or_404(Announcement, pk=pk) if pk else None
    error = ""
    if request.method == "POST":
        title, body = (request.POST.get("title") or "").strip()[:140], (request.POST.get("body") or "").strip()[:2000]
        if not title or not body:
            error = "Give the notice a title and some words."
        else:
            roles = [r for r in request.POST.getlist("audience") if r in ALL_ROLES]
            try:
                expires = date.fromisoformat(request.POST.get("expires_on") or "")
            except ValueError:
                expires = None
            level = "important" if request.POST.get("level") == "important" else "normal"
            if a is None:
                a = Announcement.objects.create(title=title, body=body, audience=",".join(roles), level=level, pinned=request.POST.get("pinned") == "on",
                                                expires_on=expires, created_by=request.user)
                from apps.accounts.models import User

                from .services import notify

                sent = 0
                for u in User.objects.filter(is_active=True):
                    if u.pk != request.user.pk and a.visible_to(u):
                        sent += bool(notify(u, f"Notice: {a.title}", body[:140], "/notifications/board/", "warning" if a.level == "important" else "info", push=a.level == "important"))
                messages.success(request, f"Posted. {sent} people were notified.")
            else:
                a.title, a.body, a.audience, a.level, a.pinned, a.expires_on = title, body, ",".join(roles), level, request.POST.get("pinned") == "on", expires
                a.save()
                messages.success(request, "Notice updated.")
            return JsonResponse({"ok": True}) if _is_modal(request) else redirect("notifications:board")
    ctx = {"a": a, "roles": ALL_ROLES, "error": error, "posted": request.POST, "selected": (request.POST.getlist("audience") if request.method == "POST" else (a.roles if a else []))}
    if _is_modal(request):
        return render(request, "notifications/board_modal.html", ctx, status=422 if error else 200)
    return render(request, "notifications/board_page.html", ctx)


@require_POST
@login_required
def board_delete(request, pk):
    from django.core.exceptions import PermissionDenied

    from .models import Announcement

    if not _can_post(request.user):
        raise PermissionDenied
    get_object_or_404(Announcement, pk=pk).delete()
    from django.contrib import messages

    messages.success(request, "Notice removed.")
    return redirect("notifications:board")
