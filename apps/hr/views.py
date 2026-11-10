from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import module_required
from apps.people.models import Department, Employee

from . import services
from .forms import HolidayForm, LeaveRequestForm
from .models import AttendancePolicy, AttendanceRecord, LeavePolicy, LeaveRequest, LeaveType, OfficeLocation, PublicHoliday


def _employee(user):
    return Employee.objects.filter(user=user).first()


def _decidable(user):
    qs = LeaveRequest.objects.select_related("employee__department", "employee__manager", "leave_type")
    if user.is_superuser or user.has_role(R.ADMIN, R.COO, R.DIRECTOR):
        return qs
    return qs.filter(employee__manager__user=user)


@login_required
def my_leave(request):
    emp = _employee(request.user)
    if emp is None:
        return render(request, "hr/my_leave.html", {"emp": None})
    form = LeaveRequestForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            services.request_leave(emp, cd["leave_type"], cd["start_date"], cd["end_date"], half_day=cd["half_day"], reason=cd["reason"],
                                   document=cd["document"], user=request.user)
            messages.success(request, "Leave requested. Your manager has been notified.")
            return redirect("hr:my")
        except ValueError as e:
            form.add_error(None, str(e))
    balances = [(t, services.balance(emp, t)) for t in LeaveType.objects.filter(active=True, capped=True)]
    reqs = emp.leave_requests.select_related("leave_type")[:30]
    return render(request, "hr/my_leave.html", {"emp": emp, "form": form, "balances": balances, "reqs": reqs, "today": timezone.localdate()})


@login_required
@require_POST
def cancel(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk, employee__user=request.user)
    try:
        services.request_cancel(leave, request.user, request.POST.get("reason", ""))
        messages.success(request, "Done — " + ("withdrawn." if leave.status == "withdrawn" else "waiting for your approver to confirm."))
    except (ValueError, PermissionError) as e:
        messages.error(request, str(e))
    return redirect("hr:my")


@login_required
def approvals(request):
    qs = _decidable(request.user)
    team = request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO, R.DIRECTOR) or Employee.objects.filter(manager__user=request.user).exists()
    pending = qs.filter(status__in=[LeaveRequest.PENDING, LeaveRequest.CANCEL_REQ])
    recent = qs.exclude(status__in=[LeaveRequest.PENDING, LeaveRequest.CANCEL_REQ]).order_by("-updated_at")[:20]
    return render(request, "hr/approvals.html", {"pending": pending, "recent": recent, "team": team})


@login_required
@require_POST
def decide(request, pk):
    leave = get_object_or_404(_decidable(request.user), pk=pk)
    act = request.POST.get("action")
    try:
        if leave.status == LeaveRequest.CANCEL_REQ:
            services.decide_cancel(leave, request.user, act == "approve")
        else:
            services.decide(leave, request.user, act == "approve", request.POST.get("note", ""))
        messages.success(request, "Saved.")
    except (ValueError, PermissionError) as e:
        messages.error(request, str(e))
    return redirect("hr:approvals")


@module_required("hr")
def overview(request):
    year = int(request.GET.get("year") or services.leave_year(timezone.localdate()))
    dept = request.GET.get("dept") or None
    data = services.analytics(year, dept)
    return render(request, "hr/overview.html", {"data": data, "year": year, "departments": Department.objects.all(), "dept": dept,
                                                "away": services.on_leave_today(), "months": [date(2000, m, 1) for m in range(1, 13)]})


@module_required("hr")
def settings_view(request):
    pol = LeavePolicy.get()
    form = HolidayForm(request.POST or None)
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "holiday" and form.is_valid():
            form.save()
            messages.success(request, "Holiday added.")
        elif act == "delete_holiday":
            PublicHoliday.objects.filter(pk=request.POST.get("pk")).delete()
        elif act == "policy":
            pol.carry_forward_cap = request.POST.get("carry_forward_cap") or pol.carry_forward_cap
            pol.save()
            messages.success(request, "Policy saved.")
        elif act == "office":
            try:
                OfficeLocation.objects.create(name=request.POST.get("name", "").strip()[:80] or "Office", latitude=request.POST["latitude"], longitude=request.POST["longitude"],
                                              radius_m=int(request.POST.get("radius_m") or 150))
                messages.success(request, "Office location added.")
            except Exception:
                messages.error(request, "Enter a valid latitude and longitude (use “Use my current location” while at the office).")
        elif act == "delete_office":
            OfficeLocation.objects.filter(pk=request.POST.get("pk")).delete()
        elif act == "attendance_policy":
            ap = AttendancePolicy.get()
            ap.require_office, ap.require_location = bool(request.POST.get("require_office")), bool(request.POST.get("require_location"))
            if request.POST.get("workday_start"):
                ap.workday_start = request.POST["workday_start"]
            ap.grace_minutes = int(request.POST.get("grace_minutes") or 15)
            ap.save()
            messages.success(request, "Attendance rules saved.")
        elif act == "entitlement":
            LeaveType.objects.filter(pk=request.POST.get("type")).update(default_days=request.POST.get("days") or 0)
            messages.success(request, "Entitlement updated.")
        return redirect("hr:settings")
    return render(request, "hr/settings.html", {"pol": pol, "types": LeaveType.objects.all(), "holidays": PublicHoliday.objects.order_by("date"), "form": form,
                                                        "offices": OfficeLocation.objects.all(), "ap": AttendancePolicy.get()})



# ------------------------------------------------------------------ attendance: clock in / out with location
def _clock_state(user):
    rec = services.today_record(user)
    last = AttendanceRecord.objects.filter(user=user).first()
    pol = AttendancePolicy.get()
    out = {"clocked_in": bool(rec), "require_location": pol.require_location, "require_office": pol.require_office, "has_office": OfficeLocation.objects.filter(active=True).exists()}
    show = rec or (last if last and last.date == timezone.localdate() else None)
    if show:
        out.update({"since": timezone.localtime(show.clock_in_at).strftime("%H:%M"), "hours": show.hours, "where": services.describe(show), "map": show.in_map,
                    "late": show.late, "at_office": show.in_at_office, "out_at": timezone.localtime(show.clock_out_at).strftime("%H:%M") if show.clock_out_at else ""})
    return out


@login_required
def clock_status(request):
    return JsonResponse(_clock_state(request.user))


@login_required
def clock_action(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    d = request.POST
    try:
        if d.get("action") == "out":
            rec = services.clock_out(request.user, d.get("lat"), d.get("lng"), d.get("accuracy"))
            msg = f"Clocked out at {timezone.localtime(rec.clock_out_at):%H:%M} · {rec.hours:g} h today"
        else:
            rec = services.clock_in(request.user, d.get("lat"), d.get("lng"), d.get("accuracy"))
            msg = f"Clocked in at {timezone.localtime(rec.clock_in_at):%H:%M} · {services.describe(rec)}" + (" · marked late" if rec.late else "")
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e), **_clock_state(request.user)}, status=400)
    return JsonResponse({"ok": True, "message": msg, **_clock_state(request.user)})


@login_required
def my_attendance(request):
    recs = AttendanceRecord.objects.filter(user=request.user).select_related("in_office")[:60]
    month = AttendanceRecord.objects.filter(user=request.user, date__gte=timezone.localdate().replace(day=1))
    from datetime import timedelta as td

    today = timezone.localdate()
    by_day = {}
    for r in AttendanceRecord.objects.filter(user=request.user, date__gte=today - td(days=29)):
        d = by_day.setdefault(r.date, {"hours": 0.0, "late": False, "away": False})
        d["hours"] += r.hours; d["late"] = d["late"] or r.late; d["away"] = d["away"] or not r.in_at_office
    days = [today - td(days=i) for i in range(29, -1, -1)]
    chart = {"labels": [f"{d:%d %b}" for d in days], "hours": [round(by_day.get(d, {}).get("hours", 0), 2) for d in days],
             "colors": [("#D98A22" if by_day[d]["late"] else "#9A8E86" if by_day[d]["away"] else "#1F6F6B") if d in by_day else "#EDE3D3" for d in days]}
    return render(request, "hr/my_attendance.html", {"chart": chart, "recs": recs, "days": month.values("date").distinct().count(), "hours": round(sum(r.hours for r in month), 1),
                                                     "late": month.filter(late=True).count(), "away": month.filter(in_at_office=False).count()})


@module_required("hr")
def attendance(request):
    from datetime import date

    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    recs = list(AttendanceRecord.objects.filter(date=day).select_related("user", "in_office"))
    from apps.accounts.models import User

    present = {r.user_id for r in recs}
    absent = [u for u in User.objects.filter(is_active=True).exclude(pk__in=present).order_by("full_name")] if day <= timezone.localdate() else []
    return render(request, "hr/attendance.html", {"day": day, "recs": recs, "absent": absent, "in_now": sum(1 for r in recs if r.open), "late": sum(1 for r in recs if r.late),
                                                  "away": sum(1 for r in recs if not r.in_at_office)})
