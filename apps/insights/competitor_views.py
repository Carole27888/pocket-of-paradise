"""Competitor Watch: for the Director, COO, Admin, Operations Coordinator and Sales Manager."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.forms import StyledModelForm

from apps.ict import ai

from . import competitors as svc
from .models import Competitor, MarketSearch, Snapshot

MODULES = ("intelligence", "sales", "operations")


def _need(user):
    if not svc.can_watch(user):
        raise PermissionDenied


class CompetitorForm(StyledModelForm):
    full_width_fields = ("pages", "notes", "google_query")

    class Meta:
        model = Competitor
        fields = ["name", "website", "google_query", "pages", "facebook_url", "instagram_url", "tiktok_url", "youtube_url", "is_main", "is_active", "notes"]
        widgets = {"pages": forms.Textarea(attrs={"rows": 3, "placeholder": "https://www.competitor.co.ke/safaris\nhttps://www.competitor.co.ke/offers"}),
                   "notes": forms.Textarea(attrs={"rows": 3})}

    def save(self, commit=True):
        obj = super().save(commit=False)
        if "google_query" in self.changed_data:
            obj.google_place_id = ""                      # look the profile up again
        if commit:
            obj.save()
            if obj.is_main:                               # only one "greatest competitor"
                Competitor.objects.exclude(pk=obj.pk).filter(is_main=True).update(is_main=False)
        return obj


@module_required(*MODULES)
def competitors(request):
    _need(request.user)
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "search":
            q = (request.POST.get("q") or "").strip()
            if not q:
                messages.error(request, "Type what to search for.")
                return redirect("insights:competitors")
            s = svc.search_web(request.user, q)
            return redirect(f"{request.path}?search={s.pk}#search")
        if act == "check_all":
            n = svc.check_all()
            messages.success(request, f"Checked every competitor: {n} change{'s' if n != 1 else ''} found.")
        return redirect("insights:competitors")
    rows = svc.overview()
    main = next((r for r in rows if r["c"].is_main), rows[0] if rows else None)
    shown = MarketSearch.objects.filter(pk=request.GET.get("search")).first() if (request.GET.get("search") or "").isdigit() else None
    recent = Snapshot.objects.filter(changed=True).select_related("competitor")[:12]
    return render(request, "insights/competitors.html", {
        "rows": rows, "main": main, "charts": svc.charts(main["c"]) if main else None, "recent": recent, "shown": shown,
        "searches": MarketSearch.objects.select_related("created_by")[:8], "places_ready": bool(svc.places_key()),
        "ai_ready": ai.configured(),
        "suggestions": ["Best Maasai Mara safaris this season and their prices", "Cheapest 2-day Amboseli safaris from Nairobi",
                        "New safari packages launched by Kenyan operators this month", "Top-rated safari companies in Nairobi on Google"],
        "compare": {"labels": [r["c"].name for r in rows], "values": [r["changes"] for r in rows], "label": "Changes in 30 days"}})


@module_required(*MODULES)
def competitor_form(request, pk=None):
    _need(request.user)
    obj = get_object_or_404(Competitor, pk=pk) if pk else None
    form = CompetitorForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        c = form.save(commit=False)
        if obj is None:
            c.created_by = request.user
        c.save()
        if c.is_main:
            Competitor.objects.exclude(pk=c.pk).filter(is_main=True).update(is_main=False)
        if obj is None:                                   # first look straight away, so there is a baseline to compare with
            svc.check(c, notify=False)
            messages.success(request, f"{c.name} added and checked for the first time. From now on every change is shown here.")
        else:
            messages.success(request, f"{c.name} saved.")
        return redirect("insights:competitor", pk=c.pk)
    return render(request, "components/form_page.html", {"form": form, "title": f"Edit {obj}" if obj else "Watch a competitor",
                                                        "cancel_url": f"/insights/competitors/{obj.pk}/" if obj else "/insights/competitors/"})


@module_required(*MODULES)
def competitor_detail(request, pk):
    _need(request.user)
    c = get_object_or_404(Competitor, pk=pk)
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "check":
            snaps = svc.check(c)
            n = sum(1 for s in snaps if s.ok and (s.changed or s.new_reviews))
            bad = [s for s in snaps if not s.ok]
            messages.success(request, f"Checked {len(snaps)} source{'s' if len(snaps) != 1 else ''}: " + (f"{n} change{'s' if n != 1 else ''}." if n else "nothing new."))
            for s in bad[:3]:
                messages.error(request, f"{s.url or 'Google'}: {s.error}")
        elif act == "delete":
            name = c.name
            c.delete()
            messages.success(request, f"{name} removed from Competitor Watch.")
            return redirect("insights:competitors")
        return redirect("insights:competitor", pk=c.pk)
    pages = []
    for url in c.page_list:
        last = c.snapshots.filter(source=Snapshot.WEBSITE, url=url).first()
        pages.append({"url": url, "last": last})
    return render(request, "insights/competitor_detail.html", {
        "c": c, "pages": pages, "google": c.latest_google(), "google_last": c.snapshots.filter(source=Snapshot.GOOGLE).first(),
        "timeline": c.snapshots.filter(changed=True)[:30], "reviews": [r for s in c.snapshots.filter(source=Snapshot.GOOGLE)[:20] for r in s.new_reviews][:15],
        "charts": svc.charts(c), "places_ready": bool(svc.places_key()),
        "prices": (c.snapshots.filter(source=Snapshot.WEBSITE, ok=True).exclude(prices=[]).first() or Snapshot()).prices})


@require_POST
@module_required(*MODULES)
def search_delete(request, pk):
    _need(request.user)
    get_object_or_404(MarketSearch, pk=pk).delete()
    return redirect("insights:competitors")
