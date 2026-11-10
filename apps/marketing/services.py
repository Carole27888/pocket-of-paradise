"""Marketing logic: channels, lead ingestion, automation workflows, attribution and analytics."""
import hashlib
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.integrations.base import get_service
from apps.integrations.messaging import send_message
from apps.leads import services as lead_services
from apps.leads.models import OPEN_STAGES, Enquiry, LeadActivity, Source, Stage, source_codes

from .models import (AdSpendDaily, AutomationWorkflow, Campaign, Channel, ChannelStatsDaily, InboxMessage, LinkClick,
                     Post, Review, ReviewRequest, SocialAccount, TrackedLink)

# key, name, is_social
CHANNELS = [("facebook", "Facebook", True), ("instagram", "Instagram", True), ("tiktok", "TikTok", True),
            ("google", "Google", True), ("youtube", "YouTube", True), ("tripadvisor", "TripAdvisor", True),
            ("website", "Website", False), ("whatsapp", "WhatsApp", False), ("walk_in", "Walk-in", False),
            ("call", "Call", False), ("dm", "DM", False), ("referral", "Referral", False), ("agent", "Travel agent partner", False)]
SERVICE_FOR = {"facebook": "meta", "instagram": "meta", "tiktok": "tiktok", "google": "google_ads", "youtube": "youtube"}

WORKFLOWS = [
    ("welcome", "New enquiry welcome", "enquiry.created", "Thank the guest on WhatsApp and assign an agent",
     [("action", "Send WhatsApp welcome"), ("action", "Assign to a consultant")],
     "Hello {name}, thanks for contacting Pocket of Paradise Travel Company! {agent} will be in touch shortly about {tour}."),
    ("response-check", "Consultant response check", "enquiry.sla", "Not contacted within the response time: reassign and alert the manager",
     [("condition", "No contact after SLA minutes"), ("action", "Reassign to next agent"), ("action", "Alert Sales Manager")], ""),
    ("pre-trip", "Pre-trip reminders", "departure.upcoming", "Pickup time, meeting point and packing list at 7 days and 1 day",
     [("condition", "Departure in 7 or 1 day"), ("action", "Send WhatsApp reminder")],
     "Hi {name}, your {tour} safari leaves on {date}. Meeting point: {meeting}. Pack: sun hat, layers, binoculars, camera. See you soon!"),
    ("review-request", "Post-trip review request", "departure.completed", "Thank-you and review link after the trip",
     [("action", "Send thank-you + review link"), ("action", "Create content draft")],
     "Thank you for travelling with us, {name}! We would love your review of {tour}: https://g.page/pocketofparadise/review"),
    ("re-engagement", "Re-engage lapsed enquiries", "enquiry.lapsed", "Guests whose travel date passed without booking",
     [("condition", "Travel date passed, no booking"), ("wait", "14 days"), ("action", "Send offer")],
     "Hi {name}, we still have great {tour} departures coming up. Reply to this message for a fresh quote."),
    ("ad-spend", "Daily ad spend to Finance", "ads.daily", "Record yesterday's ad spend as a Marketing expense", [("action", "Post expense to Finance")], ""),
    ("weekend-reply", "Weekend auto-reply", "inbox.weekend", "Acknowledge messages that arrive on Saturday or Sunday",
     [("condition", "Received Sat/Sun"), ("action", "Send auto-reply")],
     "Thanks for your message! Our team is away for the weekend and will reply first thing Monday."),
]


def ensure_channels():
    if Channel.objects.count() >= len(CHANNELS):
        return
    for i, (key, name, social) in enumerate(CHANNELS):
        Channel.objects.get_or_create(key=key, defaults={"name": name, "is_social": social, "sort_order": i})


def ensure_workflows():
    for key, name, trig, desc, steps, tpl in WORKFLOWS:
        AutomationWorkflow.objects.get_or_create(key=key, defaults={
            "name": name, "trigger": trig, "description": desc, "template": tpl,
            "steps": [{"type": t, "text": x} for t, x in steps]})


def workflow(key: str) -> AutomationWorkflow:
    ensure_workflows()
    return AutomationWorkflow.objects.get(key=key)


def _render(tpl: str, **ctx) -> str:
    class Safe(dict):
        def __missing__(self, k):
            return ""

    return tpl.format_map(Safe(ctx))


# ================================================================================================ lead ingestion
def _client_ip_hash(request) -> str:
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


@transaction.atomic
def ingest_lead(*, name, phone, email="", source="website", message="", package=None, adults=2, children=0,
                travel_date=None, campaign=None, link=None, purpose="new", captured_via="api", notes_prefix=""):
    """Website form, expo capture, social and OTA leads all arrive here: one enquiry, waiting at the Front Desk, and welcomed."""
    ensure_channels()
    notes = (notes_prefix + (" " if notes_prefix and message else "") + message).strip()
    enquiry = lead_services.create_enquiry(
        client_data={"full_name": name.strip(), "phone": phone, "email": email},
        enquiry_data={"source": source, "purpose": purpose, "package": package, "adults": adults, "children": children,
                      "travel_date_from": travel_date, "notes": notes, "campaign": campaign, "tracked_link": link,
                      "captured_via": captured_via, "destination": package.destinations.first() if package else None},
        user=None, auto_assign=False)
    # Every lead lands at the Front Desk, who hand it to an agent. Automatic assignment only happens if a manager switched it on.
    from apps.sales.services import maybe_auto_assign

    maybe_auto_assign(enquiry, None)
    enquiry.refresh_from_db()
    send_welcome(enquiry)
    return enquiry


def send_welcome(enquiry: Enquiry):
    wf = workflow("welcome")
    if not wf.enabled or not enquiry.client.phone:
        return None
    agent = enquiry.assigned_agent.short_name if enquiry.assigned_agent_id else "A consultant"
    body = _render(wf.template, name=enquiry.client.full_name.split()[0], agent=agent, tour=enquiry.interest_label)
    msg = send_message("whatsapp", enquiry.client.phone, body, purpose="welcome", related=f"enquiry:{enquiry.pk}")
    wf.ran()
    return msg


def register_hooks():
    """Cross-module wiring: completed departures trigger review requests and a content draft."""
    from apps.trips import services as trip_services

    trip_services.COMPLETION_HOOKS.append(on_departure_completed)


def on_departure_completed(dep, user=None):
    from apps.sales.models import BookingStatus

    wf = workflow("review-request")
    if wf.enabled:
        for b in dep.bookings.exclude(status=BookingStatus.CANCELLED).select_related("client"):
            _, created = ReviewRequest.objects.get_or_create(booking=b, via="whatsapp", defaults={"departure": dep})
            if created and b.client.phone:
                send_message("whatsapp", b.client.phone, _render(
                    wf.template, name=b.client.full_name.split()[0], tour=dep.package.title),
                    purpose="review_request", related=f"booking:{b.pk}")
                wf.ran()
    from .models import ContentShoot

    ContentShoot.objects.get_or_create(departure=dep, defaults={
        "title": f"Footage: {dep.package.title} ({dep.start_date:%d %b})", "package": dep.package, "stage": ContentShoot.SHOT,
        "shoot_date": dep.start_date, "assigned_to": dep.guide, "shot_at": timezone.now(),
        "notes": "Auto-created when the trip completed. Edit, then link the post it became."})
    ensure_channels()
    post, created = Post.objects.get_or_create(
        departure=dep, source="trip_completed",
        defaults={"title": f"Recap: {dep.package.title} ({dep.start_date:%d %b})", "package": dep.package,
                  "caption": f"Another unforgettable {dep.package.title}! Book your own safari with Pocket of Paradise Travel Company.",
                  "status": Post.DRAFT, "format": "reel"})
    if created:
        post.channels.set(Channel.objects.filter(key__in=["instagram", "facebook", "tiktok"]))


# ================================================================================================ trip production
def advance_shoot(shoot, user=None):
    from .models import ContentShoot

    now = timezone.now()
    if shoot.stage == ContentShoot.SOURCED:
        shoot.stage, shoot.shot_at = ContentShoot.SHOT, now
    elif shoot.stage == ContentShoot.SHOT:
        if not shoot.post_id:
            raise ValueError("Link the post it was published as before marking it Posted.")
        shoot.stage, shoot.posted_at = ContentShoot.POSTED, now
    else:
        raise ValueError("Already posted.")
    shoot.stage_changed_at = now
    shoot.save()
    return shoot


def retreat_shoot(shoot, user=None):
    """Move a shoot one stage back (Posted → Shot → Sourced) when it was advanced by mistake."""
    from .models import ContentShoot

    if shoot.stage == ContentShoot.POSTED:
        shoot.stage, shoot.posted_at = ContentShoot.SHOT, None
    elif shoot.stage == ContentShoot.SHOT:
        shoot.stage, shoot.shot_at = ContentShoot.SOURCED, None
    else:
        raise ValueError("Already at the first stage.")
    shoot.stage_changed_at = timezone.now()
    shoot.save()
    return shoot


# ================================================================================================ scheduled work
def sync_channel_stats(day=None) -> int:
    ensure_channels()
    day = day or timezone.localdate() - timedelta(days=1)
    n = 0
    for ch in Channel.objects.filter(is_social=True):
        svc = get_service(SERVICE_FOR.get(ch.key, "meta"))
        acct, _ = SocialAccount.objects.get_or_create(channel=ch, defaults={"handle": f"@pocketofparadise", "connected": True})
        if not acct.connected:
            continue
        s = svc.account_stats(ch.key, day)
        ChannelStatsDaily.objects.update_or_create(channel=ch, date=day, defaults=s)
        acct.followers, acct.last_synced_at = s["followers"], timezone.now()
        acct.save(update_fields=["followers", "last_synced_at"])
        n += 1
    return n


def sync_ad_spend(day=None) -> int:
    day = day or timezone.localdate() - timedelta(days=1)
    n = 0
    for camp in Campaign.objects.filter(status=Campaign.ACTIVE, start_date__lte=day).prefetch_related("channels"):
        if camp.end_date and camp.end_date < day:
            continue
        for ch in camp.channels.filter(is_social=True):
            svc = get_service(SERVICE_FOR.get(ch.key, "meta"))
            data = svc.daily_ads(camp.name, ch.key, day)
            AdSpendDaily.objects.update_or_create(campaign=camp, channel=ch, date=day, defaults=data)
            n += 1
    return n


def post_ad_spend_to_finance() -> int:
    """Marketing spend becomes a paid Marketing expense in Finance (workflow `ad-spend`)."""
    if not workflow("ad-spend").enabled:
        return 0
    from apps.finance import services as fin

    n = 0
    for row in AdSpendDaily.objects.filter(posted_to_finance=False, spend__gt=0).select_related("campaign", "channel"):
        exp = fin.record_expense(None, date=row.date, category="marketing", payee=f"{row.channel.name} Ads",
                                 description=f"{row.campaign.name} · {row.date:%d %b}", amount=row.spend, method="card",
                                 auto_approve=True, source="marketing_ad_spend")
        row.posted_to_finance = True
        row.save(update_fields=["posted_to_finance"])
        n += 1
    if n:
        workflow("ad-spend").ran(n)
    return n


def sync_reviews() -> int:
    n = 0
    since = timezone.localdate() - timedelta(days=3)
    for source, svc_name in (("google", "google_reviews"), ("tripadvisor", "tripadvisor")):
        for r in get_service(svc_name).latest(source, since):
            _, created = Review.objects.get_or_create(source=source, external_id=r["external_id"], defaults={
                "guest_name": r["guest_name"], "rating": r["rating"], "text": r["text"], "review_date": r["review_date"]})
            n += created
    return n


def sync_inbox() -> int:
    ensure_channels()
    n = 0
    since = timezone.localdate() - timedelta(days=1)
    wf = workflow("weekend-reply")
    for ch in Channel.objects.filter(key__in=["facebook", "instagram", "tiktok"]):
        for m in get_service("inbox").fetch(ch.key, since):
            when = timezone.make_aware(timezone.datetime.combine(m["received_at"], timezone.datetime.min.time())) + timedelta(hours=10)
            msg, created = InboxMessage.objects.get_or_create(channel=ch, external_id=m["external_id"], defaults={
                "sender": m["sender"], "message": m["message"], "kind": m["kind"], "received_at": when})
            n += created
            if created and wf.enabled and when.weekday() >= 5 and m["kind"] == "message":
                reply(msg, wf.template, None, auto=True)
                wf.ran()
    return n


def reply(msg: InboxMessage, text: str, user=None, auto=False):
    msg.replied, msg.reply_text, msg.replied_at = True, text, timezone.now()
    msg.save(update_fields=["replied", "reply_text", "replied_at"])
    return msg


def convert_to_enquiry(msg: InboxMessage, user=None, phone="") -> Enquiry:
    if msg.enquiry_id:
        return msg.enquiry
    phone = phone or msg.phone
    if not phone:
        raise ValueError("Add the guest's phone number to create an enquiry.")
    source = msg.channel.key if msg.channel.key in source_codes() else "dm"
    enq = lead_services.create_enquiry(client_data={"full_name": msg.sender, "phone": phone},
                                       enquiry_data={"source": source, "notes": msg.message[:500], "captured_via": "marketing_sync"},
                                       user=user)
    msg.enquiry, msg.phone = enq, phone
    msg.save(update_fields=["enquiry", "phone"])
    return enq


def sync_ota_enquiries() -> int:
    n = 0
    since = timezone.localdate() - timedelta(days=2)
    for e in get_service("ota").enquiries(since):
        if Enquiry.objects.filter(notes__contains=e["external_id"]).exists():
            continue
        ingest_lead(name=e["name"], phone=e["phone"], email=e["email"], source="agent", adults=e["adults"],
                    message=f"{e['note']} [{e['external_id']}]", captured_via="marketing_sync")
        n += 1
    return n


def reassign_unresponsive() -> int:
    """Not contacted within the SLA: move to the next agent and alert the Sales Manager (once per lead)."""
    from apps.accounts import roles
    from apps.notifications.services import notify_roles
    from apps.sales import services as sales

    wf = workflow("response-check")
    if not wf.enabled:
        return 0
    if not sales.SalesSettings.get().auto_assign_new:     # routing is switched off in ICT: leads stay with whoever has them until a manager moves them
        return 0
    n = 0
    fresh = Enquiry.objects.filter(assigned_agent__isnull=False, created_at__gte=timezone.now() - timedelta(days=3))
    for e in sales.sla_breaches(fresh).select_related("assigned_agent", "client"):
        if e.assignments.count() >= 2:          # already reassigned once: leave it to the manager
            continue
        old = e.assigned_agent
        new, method, why = sales.pick_agent(e, exclude=(old.pk,))
        if new is None:
            continue
        lead_services.assign_agent(e, new, None, "auto", f"No response from {old.short_name}")
        notify_roles([roles.SALES_MANAGER], f"{e.reference} reassigned after no response",
                     f"{old.short_name} → {new.short_name} · {e.client.full_name}", e.get_absolute_url(), "warning",
                     dedupe_key=f"reassign:{e.pk}")
        n += 1
    if n:
        wf.ran(n)
    return n


def send_pretrip_reminders() -> int:
    from apps.trips.models import Departure, Stage as DStage

    wf = workflow("pre-trip")
    if not wf.enabled:
        return 0
    today = timezone.localdate()
    n = 0
    for dep in Departure.objects.filter(start_date__in=[today + timedelta(days=7), today + timedelta(days=1)]).exclude(
            stage__in=[DStage.CANCELLED, DStage.COMPLETED]).select_related("package"):
        days = (dep.start_date - today).days
        for b in dep.live_bookings.select_related("client"):
            key = f"pretrip:{b.pk}:{days}"
            if b.client.phone and not b.client.outbound_marker(key):
                send_message("whatsapp", b.client.phone, _render(wf.template, name=b.client.full_name.split()[0], tour=dep.package.title,
                                                                   date=f"{dep.start_date:%d %b}", meeting=dep.meeting_point),
                             purpose="pretrip", related=key)
                n += 1
    if n:
        wf.ran(n)
    return n


def reengage_lapsed() -> int:
    wf = workflow("re-engagement")
    if not wf.enabled:
        return 0
    cutoff = timezone.localdate() - timedelta(days=14)
    n = 0
    for e in Enquiry.objects.filter(stage__in=[Stage.ENQUIRY, Stage.QUOTED, Stage.HOT], travel_date_from__lt=cutoff
                                    ).select_related("client", "package")[:100]:
        key = f"reengage:{e.pk}"
        if not e.client.phone or e.client.outbound_marker(key):
            continue
        send_message("whatsapp", e.client.phone, _render(wf.template, name=e.client.full_name.split()[0], tour=e.interest_label),
                     purpose="reengage", related=key)
        n += 1
    if n:
        wf.ran(n)
    return n


def warn_expiring_tokens() -> int:
    from apps.accounts import roles
    from apps.notifications.services import notify_roles

    n = 0
    for a in SocialAccount.objects.filter(connected=True, expires_at__lt=timezone.now() + timedelta(days=7)):
        n += notify_roles([roles.MARKETING], f"{a.channel.name} connection {a.status.lower()}",
                          "Reconnect the account to keep syncing.", "/marketing/accounts/", "warning",
                          dedupe_key=f"token:{a.pk}:{timezone.localdate():%Y-%m-%d}")
    return n


def publish_due_posts() -> int:
    n = 0
    for p in Post.objects.filter(status=Post.SCHEDULED, scheduled_at__lte=timezone.now()):
        p.status, p.published_at = Post.PUBLISHED, timezone.now()
        p.published_urls = {c.key: f"https://{c.key}.example/pocketofparadise/{p.pk}" for c in p.channels.all()}
        p.save(update_fields=["status", "published_at", "published_urls"])
        n += 1
    return n


# ================================================================================================ analytics
def period_bounds(key: str):
    from apps.sales.analytics import period

    return period(key)


def channel_counts(start, end):
    """{channel_key: enquiries} for enquiries created in [start, end]."""
    return dict(Enquiry.objects.filter(created_at__date__range=(start, end)).values_list("source").annotate(n=Count("pk")))


def spend_summary(start, end, channel=None):
    qs = AdSpendDaily.objects.filter(date__range=(start, end))
    if channel:
        qs = qs.filter(channel=channel)
    t = qs.aggregate(spend=Sum("spend"), imp=Sum("impressions"), clicks=Sum("clicks"), reach=Sum("reach"), leads=Sum("leads"))
    spend, imp, clicks = t["spend"] or Decimal("0"), t["imp"] or 0, t["clicks"] or 0
    enq = Enquiry.objects.filter(created_at__date__range=(start, end))
    if channel:
        enq = enq.filter(source=channel.key)
    else:
        enq = enq.filter(campaign__isnull=False)
    from apps.sales.models import Booking, BookingStatus

    booked = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(enquiry__in=enq).count()
    n_enq = enq.count()
    return {"spend": spend, "impressions": imp, "clicks": clicks, "reach": t["reach"] or 0, "platform_leads": t["leads"] or 0,
            "ctr": round(100 * clicks / imp, 2) if imp else 0, "enquiries": n_enq, "bookings": booked,
            "cpl": round(spend / n_enq) if n_enq else None, "cpb": round(spend / booked) if booked else None}


def cumulative_by_channel(start, end, keys):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    per = defaultdict(lambda: defaultdict(int))
    for src, d, n in (Enquiry.objects.filter(created_at__date__range=(start, end), source__in=keys)
                      .values_list("source", "created_at__date").annotate(n=Count("pk"))):
        per[src][d] += n
    out = {}
    for k in keys:
        running, series = 0, []
        for d in days:
            running += per[k][d]
            series.append(running)
        out[k] = series
    return [d.strftime("%d %b") for d in days], out


def funnel(start, end):
    from apps.sales.models import Booking, BookingStatus

    imp = AdSpendDaily.objects.filter(date__range=(start, end)).aggregate(i=Sum("impressions"), c=Sum("clicks"))
    enq = Enquiry.objects.filter(created_at__date__range=(start, end))
    steps = [("Impressions", imp["i"] or 0), ("Clicks", imp["c"] or 0), ("Enquiries", enq.count()),
             ("Quotes", enq.exclude(quotation_status="not_quoted").count()),
             ("Bookings", Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(enquiry__in=enq).count()),
             ("Travelled", enq.filter(stage=Stage.COMPLETED).count())]
    top = max(steps[0][1], 1)
    return [{"label": l, "count": n, "pct": max(2, round(100 * n / top)) if n else 0} for l, n in steps]


def channel_comparison(start, end):
    from apps.sales.models import Booking, BookingStatus

    rows = []
    for ch in Channel.objects.all():
        enq = Enquiry.objects.filter(created_at__date__range=(start, end), source=ch.key)
        n = enq.count()
        if not n and not AdSpendDaily.objects.filter(channel=ch, date__range=(start, end)).exists():
            continue
        spend = AdSpendDaily.objects.filter(channel=ch, date__range=(start, end)).aggregate(t=Sum("spend"))["t"] or Decimal("0")
        books = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(enquiry__in=enq)
        rev = sum((b.total_kes for b in books), Decimal("0"))
        rows.append({"channel": ch, "enquiries": n, "spend": spend, "cpl": round(spend / n) if n and spend else None,
                     "bookings": books.count(), "revenue": rev, "roas": round(rev / spend, 1) if spend else None})
    return sorted(rows, key=lambda r: -r["enquiries"])


def interest_mix(start, end):
    qs = (Enquiry.objects.filter(created_at__date__range=(start, end), destination__isnull=False)
          .values("destination__name").annotate(n=Count("pk")).order_by("-n")[:8])
    return [(r["destination__name"], r["n"]) for r in qs]


def monthly_enquiries(months=12):
    from django.db.models.functions import TruncMonth

    first = (timezone.localdate().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    rows = dict(Enquiry.objects.filter(created_at__date__gte=first).annotate(m=TruncMonth("created_at"))
                .values_list("m").annotate(n=Count("pk")))
    out, cur = [], first
    for _ in range(months):
        out.append((cur.strftime("%b"), next((n for m, n in rows.items() if m.year == cur.year and m.month == cur.month), 0)))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def review_stats():
    qs = Review.objects.all()
    total = qs.count()
    dist = {i: qs.filter(rating=i).count() for i in (5, 4, 3, 2, 1)}
    replied = qs.filter(replied=True)
    resp_times = [(r.replied_at - timezone.make_aware(timezone.datetime.combine(r.review_date, timezone.datetime.min.time()))).total_seconds() / 3600
                  for r in replied if r.replied_at]
    avg = qs.aggregate(a=models_avg("rating"))["a"]
    return {"total": total, "average": round(avg, 2) if avg else None, "dist": dist,
            "response_rate": round(100 * replied.count() / total) if total else 0,
            "avg_response_hours": round(sum(resp_times) / len(resp_times)) if resp_times else None}


def models_avg(field):
    from django.db.models import Avg

    return Avg(field)
