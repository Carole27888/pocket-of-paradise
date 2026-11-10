"""Trip Production module: Sourced → Shot → Posted content pipeline."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.forms import StyledModelForm
from apps.core.tables import Col, TableScreen

from . import services
from .models import ContentShoot, MediaAsset

PROD = "production"
TONE = {"sourced": "warning", "shot": "info", "posted": "success"}


def _need(user):
    if not user.can_manage_production:
        raise PermissionDenied


class ShootForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = ContentShoot
        fields = ["title", "package", "departure", "assigned_to", "shoot_date", "footage_link", "post", "notes"]
        widgets = {"shoot_date": __import__("django.forms", fromlist=["DateInput"]).DateInput(),
                   "notes": __import__("django.forms", fromlist=["Textarea"]).Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.people.models import Employee

        self.fields["assigned_to"].queryset = Employee.objects.filter(is_active=True)
        self.fields["assigned_to"].label = "Shooter / editor"


@module_required(PROD)
def board(request):
    cols = []
    for stage, label in ContentShoot.STAGES:
        items = ContentShoot.objects.filter(stage=stage).select_related("assigned_to", "package", "departure")
        cols.append({"stage": stage, "label": label, "tone": TONE[stage], "items": list(items[:40]), "count": items.count()})
    return render(request, "production/board.html", {
        "cols": cols, "can_manage": request.user.can_manage_production,
        "overdue": ContentShoot.objects.filter(stage="sourced", shoot_date__lt=timezone.localdate()).count(),
        "media_count": MediaAsset.objects.count()})


class ShootList(TableScreen):
    module_key = PROD
    model = ContentShoot
    tabs_field = "stage"
    tabs_choices = ContentShoot.STAGES
    search_fields = ["title", "notes", "package__title", "departure__reference"]
    sort_fields = ["shoot_date", "stage"]
    default_sort = "shoot_date"
    title = "Shoots"
    subtitle = "Every piece of content from idea to post."
    placeholder = "Search shoots…"
    export_name = "shoots"
    columns = [
        Col("Shoot", "title", link=True, sub="package"),
        Col("Trip", "departure"),
        Col("Shoot date", "shoot_date", sort="shoot_date", kind="date"),
        Col("Assigned", "assigned_to"),
        Col("In stage", "days_label"),
        Col("Stage", "get_stage_display", kind="pill", tone=lambda o: "danger" if o.is_overdue else TONE[o.stage]),
    ]

    def get_base_queryset(self):
        qs = ContentShoot.objects.select_related("assigned_to", "package", "departure")
        self._unfiltered = qs
        v = self.request.GET.get("stage", "")
        return qs.filter(stage=v) if v else qs

    def actions(self):
        return [{"label": "New shoot", "url": reverse("production:create"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_production else []


ContentShoot.days_label = property(lambda self: f"{self.days_in_stage}d")


@module_required(PROD)
def detail(request, pk):
    s = get_object_or_404(ContentShoot.objects.select_related("assigned_to", "package", "departure", "post"), pk=pk)
    return render(request, "production/detail.html", {"s": s, "can_manage": request.user.can_manage_production})


class ShootCreate(CreateScreen):
    module_key = PROD
    edit_permission = "can_manage_production"
    model = ContentShoot
    form_class = ShootForm
    title = "New shoot"
    cancel_url_name = "production:list"


class ShootUpdate(UpdateScreen):
    module_key = PROD
    edit_permission = "can_manage_production"
    model = ContentShoot
    form_class = ShootForm


@require_POST
@module_required(PROD)
def advance(request, pk):
    _need(request.user)
    s = get_object_or_404(ContentShoot, pk=pk)
    try:
        services.advance_shoot(s, request.user)
        messages.success(request, f"Moved to {s.get_stage_display()}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or s)


@require_POST
@module_required(PROD)
def retreat(request, pk):
    _need(request.user)
    s = get_object_or_404(ContentShoot, pk=pk)
    try:
        services.retreat_shoot(s, request.user)
        messages.success(request, f"Moved back to {s.get_stage_display()}.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or s)


# ----------------------------------------------------------------------------------- overview and reports
STUCK_DAYS = 5


def _workload():
    from django.db.models import Count, Q

    from apps.people.models import Employee

    return list(Employee.objects.filter(shoots__isnull=False).distinct().annotate(
        sourced=Count("shoots", filter=Q(shoots__stage="sourced"), distinct=True),
        shot=Count("shoots", filter=Q(shoots__stage="shot"), distinct=True),
        posted=Count("shoots", filter=Q(shoots__stage="posted"), distinct=True)).order_by("-sourced", "-shot")[:12])


@module_required(PROD)
def overview(request):
    today = timezone.localdate()
    all_ = ContentShoot.objects.all()
    counts = {s: all_.filter(stage=s).count() for s, _ in ContentShoot.STAGES}
    open_ = all_.exclude(stage="posted").select_related("assigned_to", "package", "departure")
    stuck = [s for s in open_ if s.days_in_stage > STUCK_DAYS]
    month_start = today.replace(day=1)
    return render(request, "production/overview.html", {
        "counts": counts, "stages": ContentShoot.STAGES, "stuck": sorted(stuck, key=lambda s: -s.days_in_stage)[:12], "stuck_n": len(stuck),
        "stuck_days": STUCK_DAYS, "overdue": all_.filter(stage="sourced", shoot_date__lt=today).count(),
        "posted_month": all_.filter(stage="posted", stage_changed_at__date__gte=month_start).count(), "workload": _workload()})


@module_required(PROD)
def reports(request):
    from collections import Counter
    from datetime import timedelta

    today = timezone.localdate()
    posted = ContentShoot.objects.filter(stage="posted")
    months, cur = [], today.replace(day=1)
    for _ in range(6):
        months.append(cur)
        cur = (cur - timedelta(days=1)).replace(day=1)
    months.reverse()
    per_month = []
    for m in months:
        nxt = (m + timedelta(days=32)).replace(day=1)
        per_month.append(posted.filter(stage_changed_at__date__gte=m, stage_changed_at__date__lt=nxt).count())
    by_package = Counter(p for p in posted.values_list("package__title", flat=True) if p).most_common(8)
    charts = {"posted": {"labels": [m.strftime("%b") for m in months], "values": per_month, "label": "Posted"}}
    ready = [s for s in posted.exclude(shoot_date=None) if s.shoot_date]
    days = [(timezone.localtime(s.stage_changed_at).date() - s.shoot_date).days for s in ready]
    return render(request, "production/reports.html", {
        "charts": charts, "posted_6m": sum(per_month), "total_posted": posted.count(), "by_package": by_package, "avg_days": round(sum(days) / len(days), 1) if days else None,
        "months": list(zip([m.strftime("%B %Y") for m in months], per_month))[::-1]})
