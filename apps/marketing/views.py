import base64
import calendar
import io
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.catalog.models import TourPackage
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.money import compact_money, format_money
from apps.core.tables import Col, TableScreen
from apps.leads.models import Enquiry, Source, Stage
from apps.sales import analytics as sales_analytics

from . import services
from .forms import (CampaignForm, ConvertForm, HashtagForm, LeadCaptureForm, LinkForm, MediaForm, PostForm,
                    ReplyForm, WorkflowForm)
from .models import (AdSpendDaily, AutomationWorkflow, Campaign, Channel, ChannelStatsDaily, HashtagSet, InboxMessage,
                     LinkClick, MediaAsset, Post, Review, ReviewRequest, SocialAccount, TrackedLink)

MKT = "marketing"
STATUS_TONE = {"draft": "neutral", "active": "success", "paused": "warning", "completed": "info"}
POST_TONE = {"draft": "neutral", "needs_approval": "warning", "scheduled": "info", "published": "success"}


def _need_manage(user):
    if not user.can_manage_marketing:
        raise PermissionDenied


def _ctx(request, **kw):
    start, end, key = sales_analytics.period(request.GET.get("range", "month"))
    return {"ranges": sales_analytics.RANGES, "range_key": key, "start": start, "end": end,
            "can_manage": request.user.can_manage_marketing, **kw}


def _scope(user):
    """Marketing staff see only leads from the social platforms; executives see every channel."""
    from apps.accounts.permissions import is_marketing_scoped
    from apps.accounts.roles import SOCIAL_SOURCES

    return SOCIAL_SOURCES if is_marketing_scoped(user) else None


def _prev(start, end):
    n = (end - start).days + 1
    return start - timedelta(days=n), start - timedelta(days=1)


def _errors(form):
    return "; ".join(e for errs in form.errors.values() for e in errs)


# ================================================================================================ dashboard
MAIN_CHANNELS = ["facebook", "instagram", "tiktok", "google", "youtube"]
CHAN_COLOURS = {"facebook": "#1877F2", "instagram": "#E1306C", "tiktok": "#111111", "google": "#34A853", "youtube": "#FF0000"}


def _pct(now, before):
    if not before:
        return 100 if now else 0
    return round(100 * (now - before) / before)


def _scoped_keys(user):
    scope = _scope(user)
    return [k for k in MAIN_CHANNELS if not scope or k in scope]


def _campaign_perf(start, end, ps, pe, keys, leads, leads_prev):
    """Spend, impressions, clicks, click-through rate and cost per lead for the period, each against the one before."""
    def agg(a, b):
        t = AdSpendDaily.objects.filter(date__range=(a, b), channel__key__in=keys).aggregate(sp=Sum("spend"), im=Sum("impressions"), cl=Sum("clicks"))
        return {k: (t[k] or 0) for k in ("sp", "im", "cl")}

    now, before = agg(start, end), agg(ps, pe)

    def ctr(d):
        return round(100 * d["cl"] / d["im"], 1) if d["im"] else 0

    def cpl(d, n):
        return round(float(d["sp"]) / n) if n and d["sp"] else None

    return {"spend": now["sp"], "spend_pct": _pct(float(now["sp"]), float(before["sp"])),
            "impressions": now["im"], "impressions_pct": _pct(now["im"], before["im"]),
            "clicks": now["cl"], "clicks_pct": _pct(now["cl"], before["cl"]),
            "ctr": ctr(now), "ctr_pct": _pct(ctr(now), ctr(before)),
            "cpl": cpl(now, leads), "cpl_pct": _pct(cpl(now, leads) or 0, cpl(before, leads_prev) or 0)}


def _latest_blogs():
    from .models import BlogPost

    return list(BlogPost.objects.all()[:3])


@module_required(MKT)
def dashboard(request):
    services.ensure_channels()
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    ps, pe = _prev(start, end)
    scope = _scope(request.user)
    keys = _scoped_keys(request.user)
    names = {c.key: c.name for c in Channel.objects.all()}
    cur_all, prev_all = services.channel_counts(start, end), services.channel_counts(ps, pe)
    cur = {k: v for k, v in cur_all.items() if not scope or k in scope}
    prev = {k: v for k, v in prev_all.items() if not scope or k in scope}
    total, total_prev = sum(cur.values()), sum(prev.values())
    selected = request.GET.get("channel", "")
    selected = selected if selected in keys else ""
    tiles = [{"key": k, "name": names.get(k, k), "count": cur.get(k, 0), "pct": _pct(cur.get(k, 0), prev.get(k, 0))} for k in keys]
    chart_keys = [selected] if selected else keys
    labels, series = services.cumulative_by_channel(start, end, chart_keys)
    breakdown = [{"key": k, "name": names.get(k, k), "count": cur.get(k, 0), "share": round(100 * cur.get(k, 0) / total) if total else 0, "colour": CHAN_COLOURS.get(k, "#888")} for k in keys]

    # lead management table (agent + stage filters)
    enq = Enquiry.objects.filter(created_at__date__range=(start, end)).select_related("client", "assigned_agent")
    if scope:
        enq = enq.filter(source__in=scope)
    if selected:
        enq = enq.filter(source=selected)
    agent, stage = request.GET.get("agent", ""), request.GET.get("status", "")
    if agent.isdigit():
        enq = enq.filter(assigned_agent_id=int(agent))
    if stage in Stage.values:
        enq = enq.filter(stage=stage)
    from apps.people.models import Employee

    # quick stats for one channel (dropdown)
    q = request.GET.get("q", "")
    q = q if q in keys else (keys[0] if keys else "")
    qch = Channel.objects.filter(key=q).first()
    s_now, s_prev = services.spend_summary(start, end, qch), services.spend_summary(ps, pe, qch)
    weeks = []
    for i in range(4, -1, -1):
        ws = end - timedelta(days=7 * i + 6)
        we = ws + timedelta(days=6)
        weeks.append((f"Wk {5 - i}", Enquiry.objects.filter(source=q, created_at__date__range=(ws, we)).count(),
                      float(AdSpendDaily.objects.filter(channel=qch, date__range=(ws, we)).aggregate(t=Sum("spend"))["t"] or 0)))
    ctx.update(
        keys=keys, selected=selected, tiles=tiles, total=total, total_pct=_pct(total, total_prev), breakdown=breakdown, names=names,
        enquiries=enq.order_by("-created_at")[:10], enquiries_total=enq.count(), perf=_campaign_perf(start, end, ps, pe, keys, total, total_prev),
        latest_blogs=_latest_blogs(), agents=Employee.objects.filter(is_sales_agent=True, is_active=True).order_by("full_name"),
        agent=agent, stage=stage, stages=Stage.choices, q=q, qname=names.get(q, q), qleads=cur.get(q, 0), qleads_pct=_pct(cur.get(q, 0), prev.get(q, 0)),
        qspend=s_now["spend"], qspend_pct=_pct(float(s_now["spend"]), float(s_prev["spend"])), qcpl=s_now["cpl"],
        qreach=s_now["reach"], qreach_pct=_pct(s_now["reach"], s_prev["reach"]),
        top_posts=Post.objects.filter(status="published").order_by("-engagement")[:5],
        charts={"cumulative": {"labels": labels, "series": [{"label": names.get(k, k), "data": series[k]} for k in chart_keys]},
                "sources": {"labels": [b["name"] for b in breakdown], "values": [b["count"] for b in breakdown], "codes": [b["key"] for b in breakdown], "legend": False},
                "weekly": {"labels": [w[0] for w in weeks], "enquiries": [w[1] for w in weeks], "spend": [w[2] for w in weeks]}})
    return render(request, "marketing/dashboard.html", ctx)


# ================================================================================================ social & channel pages
@module_required(MKT)
def social(request):
    services.ensure_channels()
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    cur = services.channel_counts(start, end)
    cards = []
    for ch in Channel.objects.filter(key__in=MAIN_CHANNELS):
        acct = SocialAccount.objects.filter(channel=ch).first()
        stats = ChannelStatsDaily.objects.filter(channel=ch).order_by("-date")[:30]
        first, last = (stats[len(stats) - 1] if stats else None), (stats[0] if stats else None)
        eng = ChannelStatsDaily.objects.filter(channel=ch, date__range=(start, end)).aggregate(e=Sum("engagement_rate"), n=Count("pk"))
        cards.append({"ch": ch, "acct": acct, "followers": last.followers if last else (acct.followers if acct else 0),
                      "growth": (last.followers - first.followers) if first and last else 0,
                      "posts": Post.objects.filter(channels=ch, status="published", published_at__date__range=(start, end)).count(),
                      "engagement": round(eng["e"] / eng["n"], 2) if eng["n"] else None, "enquiries": cur.get(ch.key, 0),
                      "spend": AdSpendDaily.objects.filter(channel=ch, date__range=(start, end)).aggregate(t=Sum("spend"))["t"] or 0})
    hours = dict(Enquiry.objects.filter(created_at__date__range=(start - timedelta(days=60), end)).values_list("created_at__hour").annotate(n=Count("pk")))
    best = sorted(hours.items(), key=lambda kv: -kv[1])[:3]
    ctx.update(cards=cards, scheduled=Post.objects.filter(status__in=["scheduled", "needs_approval"]).order_by("scheduled_at")[:8],
               inbox=InboxMessage.objects.filter(replied=False).select_related("channel")[:5], unread=InboxMessage.objects.filter(replied=False).count(),
               best_by_channel=_best_times(start, end),
               best_hours=[f"{(h + 3) % 12 or 12} {'AM' if (h + 3) % 24 < 12 else 'PM'}" for h, _ in best])
    return render(request, "marketing/social.html", ctx)


def _best_times(start, end):
    """Per main channel, the two weekday-and-hour slots with the most enquiries in the last 90 days."""
    out = []
    names = dict(Channel.objects.values_list("key", "name"))
    for k in MAIN_CHANNELS:
        rows = Enquiry.objects.filter(source=k, created_at__date__gte=end - timedelta(days=90)).values_list("created_at", flat=True)
        slots = {}
        for t in rows:
            t = timezone.localtime(t)
            slots[(t.strftime("%a"), t.hour)] = slots.get((t.strftime("%a"), t.hour), 0) + 1
        best = sorted(slots.items(), key=lambda kv: -kv[1])[:2]
        label = " & ".join(f"{d} {h % 12 or 12}{'AM' if h < 12 else 'PM'}" for (d, h), _ in best) if best else "Not enough data yet"
        out.append({"key": k, "name": names.get(k, k), "label": label})
    return out


FOLLOWER_LABEL = {"facebook": "Page followers", "instagram": "Followers", "tiktok": "Followers", "google": "Profile views", "youtube": "Subscribers"}
CHANNEL_TABS = [("overview", "Overview"), ("posts", "Posts"), ("ads", "Ads"), ("inbox", "Inbox"), ("audience", "Audience")]


def _delta(now, before):
    """(arrow class, text) for a figure against the previous period."""
    pct = _pct(now, before)
    return {"cls": "up" if pct > 0 else "down" if pct < 0 else "", "arrow": "↑" if pct > 0 else "↓" if pct < 0 else "", "pct": abs(pct)}


@module_required(MKT)
def channel_page(request, key):
    from django.db.models import Avg

    ch = get_object_or_404(Channel, key=key)
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    if not request.GET.get("range"):                     # a channel page opens on the last 30 days, like the Realty Boris one
        start = end - timedelta(days=29)
        ctx["start"] = start
    ps, pe = _prev(start, end)
    tab = request.GET.get("tab", "overview")
    tab = tab if tab in dict(CHANNEL_TABS) else "overview"
    acct = SocialAccount.objects.filter(channel=ch).first()

    stats = ChannelStatsDaily.objects.filter(channel=ch)
    cur_stats, prev_stats = stats.filter(date__range=(start, end)), stats.filter(date__range=(ps, pe))
    first, last = cur_stats.order_by("date").first(), cur_stats.order_by("-date").first()
    followers = last.followers if last else (acct.followers if acct else 0)
    growth = (last.followers - first.followers) if first and last else 0
    reach, reach_prev = cur_stats.aggregate(r=Sum("reach"))["r"] or 0, prev_stats.aggregate(r=Sum("reach"))["r"] or 0
    eng = float(cur_stats.aggregate(e=Avg("engagement_rate"))["e"] or 0)
    eng_prev = float(prev_stats.aggregate(e=Avg("engagement_rate"))["e"] or 0)
    leads, leads_prev = (Enquiry.objects.filter(source=key, created_at__date__range=(a, b)).count() for a, b in ((start, end), (ps, pe)))
    s_now, s_prev = services.spend_summary(start, end, ch), services.spend_summary(ps, pe, ch)
    kpis = [
        {"label": FOLLOWER_LABEL.get(key, "Followers"), "value": f"{followers:,}", "delta": {"cls": "up" if growth > 0 else "down" if growth < 0 else "", "arrow": "↑" if growth >= 0 else "↓", "text": f"{abs(growth):,}"}},
        {"label": "Reach", "value": f"{reach:,}", "delta": {**_delta(reach, reach_prev), "text": None}},
        {"label": "Engagement", "value": f"{eng:.2f}%", "delta": {"cls": "", "arrow": "", "text": f"{abs(eng - eng_prev):.1f} pts"}},
        {"label": "Leads", "value": leads, "delta": {**_delta(leads, leads_prev), "text": None}},
        {"label": "Ad spend", "value": "KES " + f"{int(s_now['spend']):,}", "delta": {**_delta(float(s_now["spend"]), float(s_prev["spend"])), "text": None, "invert": True}},
        {"label": "Cost per lead", "value": ("KES " + f"{s_now['cpl']:,}") if s_now["cpl"] else "–", "delta": None},
    ]
    weeks = []
    for i in range(4, -1, -1):
        ws = end - timedelta(days=7 * i + 6)
        we = ws + timedelta(days=6)
        r = ChannelStatsDaily.objects.filter(channel=ch, date__range=(ws, we)).aggregate(r=Sum("reach"))["r"] or 0
        weeks.append((f"Wk {5 - i}", r, Enquiry.objects.filter(source=key, created_at__date__range=(ws, we)).count()))
    nat = list(Enquiry.objects.filter(source=key, created_at__date__range=(start - timedelta(days=90), end))
               .values("client__nationality").annotate(n=Count("pk")).order_by("-n")[:8])
    total_nat = sum(a["n"] for a in nat) or 1
    audience = [{"name": a["client__nationality"] or "Unknown", "n": a["n"], "share": round(100 * a["n"] / total_nat)} for a in nat]
    inbox_open = InboxMessage.objects.filter(channel=ch, replied=False).count()
    posts_qs = Post.objects.filter(channels=ch).order_by("-published_at", "-scheduled_at")
    ctx.update(
        ch=ch, acct=acct, tab=tab, tabs=CHANNEL_TABS, kpis=kpis, audience=audience[:5], audience_all=audience, inbox_open=inbox_open,
        posts=posts_qs[:5] if tab == "overview" else posts_qs[:60], posts_total=posts_qs.count(),
        messages_=InboxMessage.objects.filter(channel=ch).select_related("enquiry")[:(5 if tab == "overview" else 60)],
        ads=list(AdSpendDaily.objects.filter(channel=ch, date__range=(start, end)).values("campaign__name")
                 .annotate(spend=Sum("spend"), imp=Sum("impressions"), clicks=Sum("clicks"), leads=Sum("leads")).order_by("-spend")),
        can_manage=request.user.can_manage_marketing, reply_form=ReplyForm(), convert_form=ConvertForm(),
        charts={"weeks": {"labels": [w[0] for w in weeks], "reach": [w[1] for w in weeks], "enquiries": [w[2] for w in weeks]}})
    return render(request, "marketing/channel.html", ctx)


@require_POST
@module_required(MKT)
def boost_post(request, pk):
    _need_manage(request.user)
    post = get_object_or_404(Post, pk=pk)
    from apps.integrations.base import IntegrationLog

    IntegrationLog.objects.create(service="meta", action="boost", mode="mock", detail=f"Boost requested for {post}")
    messages.success(request, f"Boost request logged for “{post.title}” (mock mode: no spend was created).")
    return redirect(request.POST.get("next") or "marketing:social")


# ================================================================================================ inbox
@module_required(MKT)
def inbox(request):
    qs = InboxMessage.objects.select_related("channel", "enquiry")
    f = request.GET.get("filter", "open")
    if f == "open":
        qs = qs.filter(replied=False)
    elif f == "converted":
        qs = qs.filter(enquiry__isnull=False)
    if request.GET.get("channel"):
        qs = qs.filter(channel__key=request.GET["channel"])
    return render(request, "marketing/inbox.html", {
        "messages_": qs[:60], "filter": f, "channels": Channel.objects.filter(is_social=True),
        "reply_form": ReplyForm(), "convert_form": ConvertForm(), "can_manage": request.user.can_manage_marketing,
        "open_count": InboxMessage.objects.filter(replied=False).count()})


@require_POST
@module_required(MKT)
def inbox_reply(request, pk):
    _need_manage(request.user)
    msg = get_object_or_404(InboxMessage, pk=pk)
    f = ReplyForm(request.POST)
    if f.is_valid():
        services.reply(msg, f.cleaned_data["text"], request.user)
        messages.success(request, "Reply saved.")
    else:
        messages.error(request, _errors(f))
    return redirect(request.POST.get("next") or "marketing:inbox")


@require_POST
@module_required(MKT)
def inbox_convert(request, pk):
    _need_manage(request.user)
    msg = get_object_or_404(InboxMessage, pk=pk)
    f = ConvertForm(request.POST)
    if f.is_valid():
        try:
            enq = services.convert_to_enquiry(msg, request.user, f.cleaned_data["phone"])
            messages.success(request, f"Enquiry {enq.reference} created.")
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _errors(f))
    return redirect(request.POST.get("next") or "marketing:inbox")


# ================================================================================================ enquiries board
@module_required(MKT)
def enquiries(request):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    qs = Enquiry.objects.filter(created_at__date__range=(start, end)).select_related("client", "assigned_agent")
    scope = _scope(request.user)
    if scope:
        qs = qs.filter(source__in=scope)
    if request.GET.get("channel"):
        qs = qs.filter(source=request.GET["channel"])
    cols = [("New", lambda e: e.stage == "enquiry" and not e.last_contact_at), ("Contacted", lambda e: e.stage == "enquiry" and e.last_contact_at),
            ("Qualified", lambda e: e.stage == "hot"), ("Quoted", lambda e: e.stage in ("quoted", "itinerary")),
            ("Confirmed", lambda e: e.stage in ("deposit", "confirmed")), ("Closed", lambda e: e.stage == "completed")]
    rows = list(qs.order_by("-created_at")[:400])
    board = [{"label": l, "items": [e for e in rows if f(e)][:25], "count": sum(1 for e in rows if f(e))} for l, f in cols]
    lost = qs.filter(stage="lost")
    reasons = lost.exclude(lost_reason="").values("lost_reason").annotate(n=Count("pk")).order_by("-n")[:5]

    uncontacted_today = qs.filter(created_at__date=timezone.localdate(), last_contact_at__isnull=True).count()
    prev_s, prev_e = _prev(start, end)
    total_prev = Enquiry.objects.filter(created_at__date__range=(prev_s, prev_e), **({"source__in": scope} if scope else {})).count()
    deals = qs.filter(stage__in=("deposit", "confirmed", "completed")).count()
    total = qs.count()
    resp = [(e.last_contact_at - e.created_at).total_seconds() / 60 for e in rows if e.last_contact_at and e.last_contact_at >= e.created_at]
    ctx.update(total=total, total_pct=_pct(total, total_prev), new_today=qs.filter(created_at__date=timezone.localdate()).count(), uncontacted_today=uncontacted_today,
               avg_response=round(sum(resp) / len(resp)) if resp else None, deal_rate=round(100 * deals / total, 1) if total else 0.0, deals=deals,
               view=request.GET.get("view", "pipeline"), listing=rows[:100])
    ctx.update(board=board, lost_count=lost.count(), reasons=reasons, channels=Channel.objects.filter(key__in=scope) if scope else Channel.objects.all(),
               channel=request.GET.get("channel", ""), stage_choices=Stage.choices)
    return render(request, "marketing/enquiries.html", ctx)


# ================================================================================================ campaigns
class CampaignList(TableScreen):
    module_key = MKT
    model = Campaign
    tabs_field = "status"
    tabs_choices = Campaign.STATUSES
    search_fields = ["name", "audience_notes"]
    sort_fields = ["start_date", "budget", "name"]
    default_sort = "-start_date"
    title = "Campaigns"
    subtitle = "Budget, spend and what each campaign brought in."
    placeholder = "Search campaigns…"
    export_name = "campaigns"
    columns = [
        Col("Campaign", "name", sort="name", link=True, sub="channel_names"),
        Col("Budget", "budget", sort="budget", kind="money"),
        Col("Spent", "spent", kind="money", sub="used_label"),
        Col("Enquiries", "enquiry_count", kind="num"),
        Col("Cost / enquiry", "cpl_label"),
        Col("Status", "get_status_display", kind="pill", tone=lambda o: STATUS_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Campaign.objects.prefetch_related("channels", "spend_rows").annotate(_enq=Count("enquiries", distinct=True))
        self._unfiltered = qs
        v = self.request.GET.get("status", "")
        return qs.filter(status=v) if v else qs

    def actions(self):
        return [{"label": "New campaign", "url": reverse("marketing:campaign_new"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_marketing else []

    def kpis(self, qs):
        t = AdSpendDaily.objects.aggregate(s=Sum("spend"))["s"] or Decimal("0")
        return [{"label": "Active campaigns", "value": qs.filter(status="active").count(), "accent": True},
                {"label": "Total budget", "value": compact_money(qs.aggregate(b=Sum("budget"))["b"] or 0)},
                {"label": "Spent to date", "value": compact_money(t)},
                {"label": "Enquiries attributed", "value": Enquiry.objects.filter(campaign__isnull=False).count()}]




@module_required(MKT)
def campaigns(request):
    qs = Campaign.objects.prefetch_related("channels", "spend_rows").annotate(enq_n=Count("enquiries", distinct=True))
    scope = _scope(request.user)
    if scope:
        qs = qs.filter(channels__key__in=scope).distinct()
    status = request.GET.get("status", "")
    items = list(qs.filter(status=status) if status else qs)
    for c in items:
        c.cpl = round(c.spent / c.enq_n) if c.enq_n else None
    sel = next((c for c in items if str(c.pk) == request.GET.get("sel")), items[0] if items else None)
    spent_all = sum((c.spent for c in items), Decimal("0"))
    budget_all = sum((c.budget for c in items), Decimal("0"))
    leads_all = sum(c.enq_n for c in items)
    detail = None
    if sel:
        t = sel.spend_rows.aggregate(s=Sum("spend"), l=Sum("leads"), v=Sum("clicks"))
        detail = {"c": sel, "spent": sel.spent, "leads": sel.enq_n, "cpl": round(sel.spent / sel.enq_n) if sel.enq_n else None}
    return render(request, "marketing/campaigns.html", {
        "items": items, "sel": sel, "detail": detail, "status": status, "statuses": Campaign.STATUSES, "can_manage": request.user.can_manage_marketing,
        "active_n": sum(1 for c in items if c.status == "active"), "paused_n": sum(1 for c in items if c.status == "paused"), "sched_n": sum(1 for c in items if c.status == "draft"),
        "spent_all": spent_all, "budget_all": budget_all, "used_pct": round(100 * spent_all / budget_all) if budget_all else 0, "leads_all": leads_all,
        "cpl_all": round(spent_all / leads_all) if leads_all else None, "tone": STATUS_TONE})


@module_required(MKT)
def campaign_detail(request, pk):
    c = get_object_or_404(Campaign.objects.prefetch_related("channels"), pk=pk)
    enq = c.enquiries.all()
    from apps.sales.models import Booking, BookingStatus

    books = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(enquiry__in=enq)
    t = c.spend_rows.aggregate(i=Sum("impressions"), k=Sum("clicks"))
    days = list(c.spend_rows.values("date").annotate(s=Sum("spend"), l=Sum("leads")).order_by("date"))[-30:]
    return render(request, "marketing/campaign.html", {
        "c": c, "enquiry_count": enq.count(), "booking_count": books.count(), "revenue": sum((b.total_kes for b in books), Decimal("0")),
        "impressions": t["i"] or 0, "clicks": t["k"] or 0, "links": c.links.all(), "recent": enq.select_related("client")[:8],
        "can_manage": request.user.can_manage_marketing,
        "charts": {"spend": {"labels": [d["date"].strftime("%d %b") for d in days], "values": [float(d["s"]) for d in days]}}})


class CampaignCreate(CreateScreen):
    module_key = MKT
    edit_permission = "can_manage_marketing"
    model = Campaign
    form_class = CampaignForm
    title = "New campaign"
    cancel_url_name = "marketing:campaigns"


class CampaignUpdate(UpdateScreen):
    module_key = MKT
    edit_permission = "can_manage_marketing"
    model = Campaign
    form_class = CampaignForm


@require_POST
@module_required(MKT)
def campaign_action(request, pk):
    _need_manage(request.user)
    c = get_object_or_404(Campaign, pk=pk)
    a = request.POST.get("action")
    if a == "pause":
        c.status = Campaign.PAUSED
    elif a == "resume":
        c.status = Campaign.ACTIVE
    elif a == "complete":
        c.status = Campaign.DONE
    elif a == "duplicate":
        old_channels = list(c.channels.all())
        c.pk, c.status, c.name, c.start_date, c.end_date = None, Campaign.DRAFT, f"{c.name} (copy)", timezone.localdate(), None
        c.save()
        c.channels.set(old_channels)
        messages.success(request, "Campaign duplicated as a draft.")
        return redirect(c)
    c.save()
    messages.success(request, f"Campaign {c.get_status_display().lower()}.")
    return redirect(c)


# ================================================================================================ content
@module_required(MKT)
def content(request):
    today = timezone.localdate()
    try:
        first = date(int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month)), 1)
    except ValueError:
        first = today.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    posts = Post.objects.filter(scheduled_at__date__range=(first, last)).prefetch_related("channels")
    by_day = {}
    for p in posts:
        by_day.setdefault(timezone.localtime(p.scheduled_at).date(), []).append(p)
    weeks = [[{"date": d, "in_month": d.month == first.month, "posts": by_day.get(d, []), "today": d == today} for d in week]
             for week in calendar.Calendar(0).monthdatescalendar(first.year, first.month)]
    return render(request, "marketing/content.html", {
        "weeks": weeks, "first": first, "prev": (first - timedelta(days=1)).replace(day=1), "next": last + timedelta(days=1),
        "queue": Post.objects.filter(status="needs_approval").prefetch_related("channels"),
        "drafts": Post.objects.filter(status="draft").prefetch_related("channels")[:12],
        "next_up": Post.objects.filter(status="scheduled", scheduled_at__gte=timezone.now()).order_by("scheduled_at").prefetch_related("channels")[:6],
        "scheduled_count": posts.filter(status="scheduled").count(), "published_count": posts.filter(status="published").count(),
        "media_counts": {k: MediaAsset.objects.filter(kind=k).count() for k, _ in MediaAsset.KINDS},
        "hashtags": HashtagSet.objects.all()[:6], "can_manage": request.user.can_manage_marketing})


class PostList(TableScreen):
    module_key = MKT
    model = Post
    tabs_field = "status"
    tabs_choices = Post.STATUSES
    search_fields = ["title", "caption"]
    sort_fields = ["scheduled_at", "reach", "engagement"]
    default_sort = "-scheduled_at"
    title = "Posts"
    subtitle = "Every post: drafts, approvals, scheduled and published, with reach and attributed leads."
    export_name = "posts"
    columns = [
        Col("Post", "title", link=True, sub="channel_names"),
        Col("Format", "get_format_display"),
        Col("Scheduled", "scheduled_at", sort="scheduled_at", kind="datetime"),
        Col("Reach", "reach", sort="reach", kind="num"),
        Col("Engagement", "engagement", sort="engagement", kind="num"),
        Col("Leads", "leads", kind="num"),
        Col("Status", "get_status_display", kind="pill", tone=lambda o: POST_TONE[o.status]),
    ]

    def get_base_queryset(self):
        qs = Post.objects.prefetch_related("channels")
        self._unfiltered = qs
        v = self.request.GET.get("status", "")
        return qs.filter(status=v) if v else qs

    def actions(self):
        return [{"label": "New post", "url": reverse("marketing:post_new"), "style": "accent", "icon": "plus"}] \
            if self.request.user.can_manage_marketing else []




@module_required(MKT)
def post_detail(request, pk):
    p = get_object_or_404(Post.objects.prefetch_related("channels"), pk=pk)
    return render(request, "marketing/post.html", {"p": p, "can_manage": request.user.can_manage_marketing})


class PostCreate(CreateScreen):
    module_key = MKT
    edit_permission = "can_manage_marketing"
    model = Post
    form_class = PostForm
    title = "New post"
    cancel_url_name = "marketing:posts"

    def get_initial(self):
        """Opened from a day on the posting calendar: that day at 10:00, ready to be scheduled."""
        init = super().get_initial()
        try:
            d = date.fromisoformat(self.request.GET.get("date", ""))
        except ValueError:
            return init
        init["scheduled_at"] = timezone.make_aware(datetime.combine(d, time(10, 0)))
        init["status"] = Post.SCHEDULED
        return init

    def get_success_url(self):
        if self.request.GET.get("from") == "calendar" and self.object.scheduled_at:
            d = timezone.localtime(self.object.scheduled_at)
            return f"{reverse('marketing:content')}?year={d.year}&month={d.month}"
        return super().get_success_url()


@require_POST
@module_required(MKT)
def post_reschedule(request, pk):
    """Drag a post (or a draft) onto another day: same time of day, new date. Drafts become scheduled."""
    _need_manage(request.user)
    p = get_object_or_404(Post, pk=pk)
    if p.status == Post.PUBLISHED:
        return JsonResponse({"ok": False, "error": "Published posts can't be moved."}, status=400)
    try:
        day = date.fromisoformat(request.POST.get("date", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Choose a day."}, status=400)
    old = timezone.localtime(p.scheduled_at).time() if p.scheduled_at else time(10, 0)
    new = timezone.make_aware(datetime.combine(day, old))
    if new < timezone.now() and day < timezone.localdate():
        return JsonResponse({"ok": False, "error": "That day has already passed."}, status=400)
    was = p.status
    p.scheduled_at = new
    if p.status == Post.DRAFT:
        p.status = Post.SCHEDULED
    p.save()
    messages.success(request, f"“{p.title}” " + ("scheduled" if was == Post.DRAFT else "moved") + f" to {day:%a %d %b} at {old:%H:%M}.")
    return JsonResponse({"ok": True})


class PostUpdate(UpdateScreen):
    module_key = MKT
    edit_permission = "can_manage_marketing"
    model = Post
    form_class = PostForm


@require_POST
@module_required(MKT)
def post_action(request, pk):
    _need_manage(request.user)
    p = get_object_or_404(Post, pk=pk)
    a = request.POST.get("action")
    if a == "approve" and p.status == Post.NEEDS:
        p.status, p.approved_by = Post.SCHEDULED, request.user
        if not p.scheduled_at:
            p.scheduled_at = timezone.now() + timedelta(hours=2)
        p.save()
        messages.success(request, "Approved and scheduled.")
    elif a == "submit" and p.status == Post.DRAFT:
        p.status = Post.NEEDS
        p.scheduled_at = p.scheduled_at or timezone.now() + timedelta(days=1)
        p.save()
        messages.success(request, "Sent for approval.")
    elif a == "reject" and p.status == Post.NEEDS:
        p.status = Post.DRAFT
        p.save()
        messages.success(request, "Returned to draft.")
    return redirect(request.POST.get("next") or p)


@module_required(MKT, "production")
def media(request):
    form = MediaForm(request.POST or None, request.FILES or None)
    hform = HashtagForm(prefix="h")
    if request.method == "POST":
        if not request.user.can_manage_production:
            raise PermissionDenied
        if "h-name" in request.POST:
            hform = HashtagForm(request.POST, prefix="h")
            if hform.is_valid():
                hform.save()
                messages.success(request, "Hashtag set saved.")
                return redirect("marketing:media")
        elif form.is_valid():
            form.save()
            messages.success(request, "Added to the library.")
            return redirect("marketing:media")
    return render(request, "marketing/media.html", {
        "form": form, "hform": hform, "assets": MediaAsset.objects.select_related("package")[:60],
        "counts": {l: MediaAsset.objects.filter(kind=k).count() for k, l in MediaAsset.KINDS}, "hashtags": HashtagSet.objects.all(),
        "can_manage": request.user.can_manage_production})


# ================================================================================================ reviews
@module_required(MKT)
def reviews(request):
    qs = Review.objects.select_related("package", "departure")
    if request.GET.get("source"):
        qs = qs.filter(source=request.GET["source"])
    if request.GET.get("rating", "").isdigit():
        qs = qs.filter(rating=int(request.GET["rating"]))
    if request.GET.get("unreplied"):
        qs = qs.filter(replied=False)
    by_pkg = (Review.objects.exclude(package__isnull=True).values("package__title").annotate(n=Count("pk"), a=Sum("rating"))
              .order_by("-n")[:6])
    series = []
    for i in range(5, -1, -1):
        m = (timezone.localdate().replace(day=1) - timedelta(days=31 * i)).replace(day=1)
        rs = Review.objects.filter(review_date__year=m.year, review_date__month=m.month)
        series.append((m.strftime("%b"), round(sum(r.rating for r in rs) / rs.count(), 2) if rs.count() else 0))
    if request.GET.get("export") == "csv":
        import csv

        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="reviews.csv"'
        w = csv.writer(resp)
        w.writerow(["Date", "Source", "Guest", "Rating", "Review", "Replied"])
        for r in qs:
            w.writerow([r.review_date, r.get_source_display(), r.guest_name, r.rating, r.text, r.replied])
        return resp
    return render(request, "marketing/reviews.html", {
        "reviews": qs[:50], "stats": services.review_stats(), "by_pkg": [(r["package__title"], r["n"], round(r["a"] / r["n"], 1)) for r in by_pkg],
        "sources": Review.SOURCES, "reply_form": ReplyForm(), "can_manage": request.user.can_manage_marketing,
        "pending_requests": ReviewRequest.objects.filter(review__isnull=True).count(),
        "charts": {"trend": {"labels": [s[0] for s in series], "values": [s[1] for s in series], "label": "Average rating"}}})


@require_POST
@module_required(MKT)
def review_reply(request, pk):
    _need_manage(request.user)
    r = get_object_or_404(Review, pk=pk)
    f = ReplyForm(request.POST)
    if f.is_valid():
        r.replied, r.reply_text, r.replied_at = True, f.cleaned_data["text"], timezone.now()
        r.save()
        messages.success(request, "Reply posted.")
    else:
        messages.error(request, _errors(f))
    return redirect("marketing:reviews")


@require_POST
@module_required(MKT)
def review_requests(request):
    """Send review requests for trips completed in the last 14 days that haven't been asked yet."""
    _need_manage(request.user)
    from apps.trips.models import Departure

    n = 0
    for dep in Departure.objects.filter(stage="completed", completed_at__gte=timezone.now() - timedelta(days=14)):
        before = ReviewRequest.objects.filter(departure=dep).count()
        services.on_departure_completed(dep, request.user)
        n += ReviewRequest.objects.filter(departure=dep).count() - before
    messages.success(request, f"{n} review request(s) sent.")
    return redirect("marketing:reviews")


# ================================================================================================ analytics
@module_required(MKT)
def analytics(request):
    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    months = services.monthly_enquiries(12)
    comp = services.channel_comparison(start, end)
    if _scope(request.user):
        comp = [r for r in comp if r["channel"].key in _scope(request.user)]
    interest = services.interest_mix(start, end)
    ctx.update(funnel=services.funnel(start, end), comp=comp, interest=interest, summary=services.spend_summary(start, end),
               charts={"monthly": {"labels": [m[0] for m in months], "values": [m[1] for m in months], "label": "Enquiries"}})
    return render(request, "marketing/analytics.html", ctx)


# ================================================================================================ accounts, links, assignment, automation, integrations
@module_required(MKT)
def accounts(request):
    services.ensure_channels()
    rows = []
    for ch in Channel.objects.filter(key__in=MAIN_CHANNELS):
        acct, _ = SocialAccount.objects.get_or_create(channel=ch)
        rows.append(acct)
    return render(request, "marketing/accounts.html", {
        "accounts": rows, "expiring": [a for a in rows if a.status in ("Expires soon", "Expired")],
        "can_manage": request.user.can_manage_marketing})


@require_POST
@module_required(MKT)
def account_action(request, pk):
    _need_manage(request.user)
    a = get_object_or_404(SocialAccount, pk=pk)
    if request.POST.get("action") == "connect":
        a.connected, a.handle = True, request.POST.get("handle", "").strip() or "@pocketofparadise"
        a.access_token = "mock-token-" + timezone.now().strftime("%Y%m%d%H%M")
        a.expires_at = timezone.now() + timedelta(days=60)
        messages.success(request, f"{a.channel.name} connected (mock mode).")
    else:
        a.connected, a.access_token, a.expires_at = False, "", None
        messages.success(request, f"{a.channel.name} disconnected.")
    a.save()
    return redirect("marketing:accounts")


def _qr_svg(url: str) -> str:
    try:
        import qrcode
        import qrcode.image.svg

        img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


@module_required(MKT)
def links(request):
    form = LinkForm(request.POST or None)
    if request.method == "POST":
        _need_manage(request.user)
        if form.is_valid():
            link = form.save(commit=False)
            link.created_by = request.user
            link.save()
            messages.success(request, f"Link created: {request.build_absolute_uri(link.short_path)}")
            return redirect("marketing:link", pk=link.pk)
    rows = []
    for l in TrackedLink.objects.select_related("campaign", "channel").annotate(_c=Count("clicks", distinct=True),
                                                                              _e=Count("enquiries", distinct=True)):
        rows.append({"l": l, "clicks": l._c, "enquiries": l._e, "rate": round(100 * l._e / l._c, 1) if l._c else 0})
    return render(request, "marketing/links.html", {"form": form, "rows": rows, "can_manage": request.user.can_manage_marketing})


@module_required(MKT)
def link_detail(request, pk):
    l = get_object_or_404(TrackedLink.objects.select_related("campaign", "channel"), pk=pk)
    full = request.build_absolute_uri(l.short_path)
    days = list(l.clicks.values("at__date").annotate(n=Count("pk")).order_by("at__date"))[-30:]
    return render(request, "marketing/link.html", {
        "l": l, "full": full, "qr": _qr_svg(full), "clicks": l.clicks.count(), "enquiries": l.enquiries.count(),
        "recent": l.enquiries.select_related("client")[:8],
        "charts": {"clicks": {"labels": [d["at__date"].strftime("%d %b") for d in days], "values": [d["n"] for d in days], "label": "Clicks"}}})


def track_redirect(request, slug):
    """Public short link: log the click, remember it for attribution, redirect with UTM tags."""
    link = get_object_or_404(TrackedLink, slug=slug)
    LinkClick.objects.create(link=link, user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                             referrer=request.META.get("HTTP_REFERER", "")[:200])
    dest = link.utm
    resp = HttpResponseRedirect(dest)
    resp.set_cookie("ds_link", link.slug, max_age=60 * 60 * 24 * 30, samesite="Lax", httponly=True)
    return resp


@module_required(MKT)
def assignment(request):
    if not request.user.can_distribute_leads:        # distributing leads is for Front Desk, the Sales Manager, COO and CEO
        raise PermissionDenied
    from apps.sales import services as sales_services
    from apps.sales.models import AssignmentRule, SalesSettings

    cfg = SalesSettings.get()
    return render(request, "marketing/assignment.html", {
        "cfg": cfg, "rules": AssignmentRule.objects.select_related("agent", "destination"),
        "agents": sales_services.available_agents(), "unassigned": Enquiry.objects.filter(assigned_agent__isnull=True, stage__in=["enquiry", "quoted", "hot"]).count()})


@module_required(MKT)
def automation(request):
    services.ensure_workflows()
    if request.method == "POST":
        _need_manage(request.user)
        wf = get_object_or_404(AutomationWorkflow, pk=request.POST.get("id"))
        if request.POST.get("action") == "toggle":
            wf.enabled = not wf.enabled
            wf.save(update_fields=["enabled"])
            messages.success(request, f"“{wf.name}” {'enabled' if wf.enabled else 'disabled'}.")
        elif request.POST.get("action") == "template":
            f = WorkflowForm(request.POST)
            if f.is_valid():
                wf.template = f.cleaned_data["template"]
                wf.save(update_fields=["template"])
                messages.success(request, "Message text saved.")
        return redirect("marketing:automation")
    from apps.notifications.models import OutboundMessage

    return render(request, "marketing/automation.html", {
        "flows": AutomationWorkflow.objects.all(), "can_manage": request.user.can_manage_marketing,
        "recent": OutboundMessage.objects.all()[:10]})


INTEGRATIONS = [
    ("Finance", "Internal", "Ad spend becomes a Marketing expense; receipts and invoices flow both ways.", "internal"),
    ("Sales / CRM", "Internal", "Every enquiry lands in the shared pipeline and is assigned instantly.", "internal"),
    ("OTA & portals", "ota", "SafariBookings, TripAdvisor, GetYourGuide, Viator enquiries.", "ota"),
    ("WhatsApp Business API", "whatsapp", "Welcome messages, reminders and review requests.", "whatsapp"),
    ("SMS gateway", "sms", "Reminders and alerts (Africa's Talking compatible).", "sms"),
    ("E-mail", "email", "Reminders and campaigns via your SMTP (EMAIL_URL).", "email"),
    ("Meta (Facebook & Instagram)", "meta", "Account stats, ad spend, messages.", "meta"),
    ("TikTok", "tiktok", "Account stats and ad spend.", "tiktok"),
    ("Google Ads", "google_ads", "Ad spend, clicks, leads.", "google_ads"),
    ("YouTube", "youtube", "Channel stats and ad spend.", "youtube"),
    ("Google Reviews", "google_reviews", "Review feed and replies.", "google_reviews"),
    ("TripAdvisor", "tripadvisor", "Review feed.", "tripadvisor"),
    ("Website enquiry form", "Webhook", "POST JSON to the webhook URL with the secret header.", "webhook"),
]


@module_required(MKT)
def integrations(request):
    import os

    from django.conf import settings as dj

    from apps.integrations.models import IntegrationLog

    rows = []
    for name, svc, desc, kind in INTEGRATIONS:
        if kind in ("internal", "webhook"):
            status, mode = ("Active" if kind == "internal" or dj.WEBHOOK_SECRET else "Set WEBHOOK_SECRET"), kind
        else:
            mode = os.environ.get(f"{svc.upper()}_MODE") or dj.INTEGRATIONS_MODE
            status = "Mock data" if mode == "mock" else "Live"
        last = IntegrationLog.objects.filter(service=svc).first()
        rows.append({"name": name, "desc": desc, "status": status, "mode": mode, "last": last, "svc": svc, "kind": kind})
    return render(request, "marketing/integrations.html", {
        "rows": rows, "webhook_url": request.build_absolute_uri(reverse("marketing:webhook_enquiry")),
        "capture_url": request.build_absolute_uri(reverse("marketing:capture")), "secret_set": bool(dj.WEBHOOK_SECRET),
        "recent": IntegrationLog.objects.all()[:12], "can_manage": request.user.can_manage_marketing})


@require_POST
@module_required(MKT)
def integration_sync(request):
    _need_manage(request.user)
    n = {"stats": services.sync_channel_stats(), "ads": services.sync_ad_spend(), "reviews": services.sync_reviews(),
         "messages": services.sync_inbox(), "ota": services.sync_ota_enquiries()}
    messages.success(request, "Sync complete: " + ", ".join(f"{v} {k}" for k, v in n.items()) + " (mock data).")
    return redirect("marketing:integrations")


# ================================================================================================ public endpoints
def _rate_limited(request, bucket: str, limit: int = 12, window: int = 3600) -> bool:
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
    key = f"rl:{bucket}:{ip}"
    n = cache.get(key, 0) + 1
    cache.set(key, n, window)
    return n > limit


def capture(request):
    """Public lead form for the website and expo stands: no login, honeypot + rate limit, attributed to the event."""
    services.ensure_channels()
    form = LeadCaptureForm(request.POST or None, initial={"event": request.GET.get("event", "")})
    done = False
    if request.method == "POST":
        if _rate_limited(request, "capture"):
            return HttpResponse("Too many submissions from this device. Please try again later.", status=429)
        if form.is_valid():
            d = form.cleaned_data
            if d["website"]:                       # honeypot filled: pretend success, create nothing
                done = True
            else:
                event = (d["event"] or request.GET.get("event", "")).strip()[:80]
                campaign = None
                if event:
                    campaign, _ = Campaign.objects.get_or_create(
                        name=f"Expo: {event}", defaults={"objective": "expo", "status": Campaign.ACTIVE})
                link = TrackedLink.objects.filter(slug=request.COOKIES.get("ds_link", "")).first()
                if link and not campaign:
                    campaign = link.campaign
                services.ingest_lead(name=d["full_name"], phone=d["phone"], email=d["email"], source="website",
                                     message=d["message"], package=d["package"], adults=d["adults"], travel_date=d["travel_date"],
                                     campaign=campaign, link=link, captured_via="api",
                                     notes_prefix=f"[{event}]" if event else "")
                done = True
    return render(request, "marketing/capture.html", {"form": form, "done": done, "event": request.GET.get("event", "")})


@csrf_exempt
def webhook_enquiry(request):
    """Kept for existing integrations: the same intake as /webhooks/lead/ (ICT → Website & Webhooks)."""
    from apps.ict.webhooks import receive_website

    return receive_website(request)


# ================================================================================================ lead quality and lead lifetime
@module_required(MKT)
def lead_quality(request):
    from apps.accounts.roles import SOCIAL_SOURCES

    from . import leadquality as lq

    ctx = _ctx(request)
    start, end = ctx["start"], ctx["end"]
    rows = lq.quality_table(start, end)
    channel, stage = request.GET.get("channel", ""), request.GET.get("stage", "")
    ctx.update(rows=rows, steps=[lq.LABEL[s.value] for s in lq.ORDER], leads=lq.recent_lifetimes(start, end, channel=channel, stage=stage), channel=channel, stage=stage,
               stage_choices=Stage.choices, channels=[(r["source"], r["name"]) for r in rows],
               totals={"leads": sum(r["leads"] for r in rows), "booked": sum(r["booked"] for r in rows), "lost": sum(r["lost"] for r in rows),
                       "value": sum((r["won_value"] for r in rows), 0)},
               chart={"labels": [r["name"] for r in rows], "datasets": [{"label": lq.LABEL[s.value], "data": [r["steps"][i] for r in rows]} for i, s in enumerate(lq.ORDER) if i in (0, 1, 3, 4, 5)]})
    return render(request, "marketing/lead_quality.html", ctx)


@module_required(MKT)
def lead_journey(request, pk):
    """One lead's life from first message to now. No phone or e-mail: Marketing tracks the journey, Sales owns the conversation."""
    from apps.accounts.roles import SOCIAL_SOURCES

    from . import leadquality as lq

    e = get_object_or_404(Enquiry.objects.select_related("client", "assigned_agent", "campaign", "tracked_link"), pk=pk)
    if _scope(request.user) and e.source not in _scope(request.user):
        raise PermissionDenied
    from apps.sales.models import Booking

    life = lq.lifetime(e)
    life["reached_label"] = lq.LABEL[lq.ORDER[life["reached"]].value]
    booking = Booking.objects.filter(enquiry=e).first()
    return render(request, "marketing/lead_journey.html", {"e": e, "life": life, "booking": booking, "order": [lq.LABEL[s.value] for s in lq.ORDER],
                                                           "activities": e.activities.exclude(activity_type="note").select_related("user")[:40], "can_manage": request.user.can_manage_marketing})


# ---------------------------------------------------------------- website blogs
@module_required("marketing")
def blogs(request):
    from django import forms as dj_forms

    from .models import BlogPost, BlogSource

    source = BlogSource.load()
    can_edit = request.user.can_manage_marketing
    if request.method == "POST" and can_edit:
        source.url = (request.POST.get("url") or "").strip()[:200]
        source.is_active = bool(request.POST.get("is_active"))
        try:
            dj_forms.URLField(required=False).clean(source.url)
        except dj_forms.ValidationError:
            messages.error(request, "That is not a valid web address.")
        else:
            source.save(update_fields=["url", "is_active"])
            messages.success(request, "Blog address saved.")
        return redirect("marketing:blogs")
    posts = BlogPost.objects.all()
    return render(request, "marketing/blogs.html", {"source": source, "posts": posts[:60], "count": posts.count(), "can_edit": can_edit})


@require_POST
@module_required("marketing")
def blogs_fetch(request):
    from . import blogs as blog_reader

    if not request.user.can_manage_marketing:
        raise PermissionDenied
    try:
        r = blog_reader.run()
        messages.success(request, f"Blog checked: {r['total']} posts found, {r['new']} new, {r['updated']} updated.")
    except blog_reader.BlogError as exc:
        messages.error(request, str(exc))
    return redirect("marketing:blogs")


# ---------------------------------------------------------------- agents: who answers marketing leads, and how fast
@module_required(MKT)
def agents(request):
    from collections import defaultdict

    from django.db.models import Count, OuterRef, Subquery

    from apps.accounts.roles import SOCIAL_SOURCES
    from apps.leads.models import LeadActivity, LeadAssignment
    from apps.people.models import Employee

    start, end, key = sales_analytics.period(request.GET.get("range", "month"))
    first = (LeadActivity.objects.filter(enquiry=OuterRef("enquiry"), activity_type__in=LeadActivity.CONTACT_TYPES,
                                         timestamp__gte=OuterRef("assigned_at")).order_by("timestamp").values("timestamp")[:1])
    asg = (LeadAssignment.objects.filter(assigned_at__date__gte=start, assigned_at__date__lte=end, enquiry__source__in=SOCIAL_SOURCES,
                                         agent__isnull=False).annotate(ft=Subquery(first)).values_list("agent_id", "assigned_at", "ft", "enquiry__stage"))
    by = defaultdict(lambda: {"leads": 0, "answered": 0, "mins": [], "won": 0})
    for agent_id, at, ft, stage in asg:
        r = by[agent_id]
        r["leads"] += 1
        if ft:
            r["answered"] += 1
            r["mins"].append((ft - at).total_seconds() / 60)
        if stage in (Stage.CONFIRMED, Stage.COMPLETED):
            r["won"] += 1
    names = {e.pk: e for e in Employee.objects.filter(pk__in=by.keys())}
    rows = sorted(({"agent": names[k], "leads": v["leads"], "answered": v["answered"],
                    "response": round(sum(v["mins"]) / len(v["mins"])) if v["mins"] else None, "won": v["won"],
                    "conversion": round(100 * v["won"] / v["leads"]) if v["leads"] else 0} for k, v in by.items() if k in names),
                  key=lambda r: (r["response"] is None, r["response"] or 0))
    return render(request, "marketing/agents.html", {"rows": rows, "ranges": sales_analytics.RANGES, "range_key": key, "start": start, "end": end})
