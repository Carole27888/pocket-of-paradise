import hashlib
import hmac
import json
import os
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import csrf_exempt as _csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from django.utils import timezone

from apps.accounts.permissions import module_required
from apps.system.services import get_credential

from apps.schedule.services import parse_local

from . import ai, assistant, services, whatsapp
from .models import BotConversation, BotSettings, KnowledgeEntry


def _can_see_chats(user):
    """ICT-module roles, plus Sales Managers (agents don't read other people's customer chats)."""
    from apps.core.modules import user_can_access_module

    return user_can_access_module(user, "ict") or user.has_role(R.SALES_MANAGER)


def _can_configure(user):
    return user.is_superuser or user.has_role(R.ADMIN, R.ICT_OFFICER, R.COO)


@module_required("ict", "sales")
def chats(request):
    if not _can_see_chats(request.user):
        raise PermissionDenied
    status = request.GET.get("status") or ""
    qs = BotConversation.objects.select_related("taken_by", "client")
    if status:
        qs = qs.filter(status=status)
    return render(request, "ict/chats.html", {"items": qs[:100], "status": status, "ai_ready": ai.configured(), "cfg": BotSettings.get()})


@module_required("ict", "sales")
def chat(request, pk):
    if not _can_see_chats(request.user):
        raise PermissionDenied
    conv = get_object_or_404(BotConversation, pk=pk)
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "take":
            services.take_over(conv, request.user)
        elif act == "resume":
            services.resume_bot(conv)
        elif act == "close":
            conv.status = BotConversation.CLOSED
            conv.save(update_fields=["status"])
        elif act == "send" and request.POST.get("body", "").strip():
            if conv.status != BotConversation.HUMAN:
                services.take_over(conv, request.user)
            services.reply(conv, request.POST["body"].strip()[:1000], user=request.user)
        return redirect("ict:chat", pk=pk)
    if conv.unread:
        conv.unread = 0
        conv.save(update_fields=["unread"])
    return render(request, "ict/chat.html", {"conv": conv, "msgs": conv.messages.select_related("user")})


@module_required("ict")
def analysis(request):
    days = int(request.GET.get("days") or 30)
    return render(request, "ict/analysis.html", {"a": services.analysis(days), "days": days})


def _provider_rows():
    """One card per integration: the fields, whether each is saved, and the last test result."""
    from apps.system import services as sys_services
    from apps.system.models import IntegrationCredential

    rows = []
    for key, (label, fields, env, powers) in sys_services.PROVIDERS.items():
        row = IntegrationCredential.objects.filter(provider=key).first()
        shown = []
        for name, flabel, secret in fields:
            if key == "ai" and name == "prefer":
                continue                       # chosen in the "Which AI" box below
            val = sys_services.get_credential(key, name)
            shown.append({"name": name, "label": flabel, "secret": secret, "has": bool(val),
                          "display": sys_services.mask(val) if secret else val,
                          "from_env": bool(val) and not (row and row.values.get(name))})
        rows.append({"key": key, "label": label, "powers": powers, "fields": shown, "row": row,
                     "configured": any(f["has"] for f in shown)})
    return rows


AI_GROUP = ("ai", "openai", "whatsapp")
MSG_GROUP = ("sms", "brevo", "webhooks", "mpesa")


@module_required("ict")
def settings_view(request):
    """API keys and the bot, in one place."""
    if not _can_configure(request.user):
        raise PermissionDenied
    cfg = BotSettings.get()
    if request.method == "POST":
        cfg.bot_name = request.POST.get("bot_name", cfg.bot_name)[:40]
        cfg.greeting = request.POST.get("greeting", cfg.greeting)[:300]
        cfg.extra_instructions = request.POST.get("extra_instructions", "")[:2000]
        cfg.fallback_message = request.POST.get("fallback_message", "")[:1000]
        cfg.handoff_alert_roles = request.POST.get("handoff_alert_roles", cfg.handoff_alert_roles)[:120]
        try:
            cfg.max_bot_replies = max(1, min(int(request.POST.get("max_bot_replies") or 12), 50))
        except ValueError:
            pass
        cfg.save()
        messages.success(request, "Bot settings saved.")
        return redirect("ict:settings")
    rows = _provider_rows()
    from apps.system import services as sys_services

    return render(request, "ict/settings.html", {
        "cfg": cfg, "guardrails": services.GUARDRAILS, "ai_ready": ai.configured(), "wa_ready": bool(get_credential("whatsapp", "access_token")),
        "ai_rows": [r for r in rows if r["key"] in AI_GROUP], "msg_rows": [r for r in rows if r["key"] in MSG_GROUP],
        "social_rows": [r for r in rows if r["key"] not in AI_GROUP + MSG_GROUP],
        "active_ai": ai.active_provider(), "prefer": (sys_services.get_credential("ai", "prefer") or "claude").lower(),
        "mode": __import__("django.conf", fromlist=["settings"]).settings.INTEGRATIONS_MODE})


@require_POST
@module_required("ict")
def bot_toggle(request):
    if not _can_configure(request.user):
        raise PermissionDenied
    cfg = BotSettings.get()
    cfg.enabled = not cfg.enabled
    cfg.save(update_fields=["enabled"])
    messages.success(request, "AI auto-reply is now ON." if cfg.enabled else "AI auto-reply is now OFF. Messages are still captured as leads.")
    nxt = request.POST.get("next") or ""
    return redirect(nxt if nxt.startswith("/ict/") else "ict:dashboard")


@require_POST
@module_required("ict")
def routing_toggle(request):
    """Automatic lead assignment on/off. Off: leads still arrive but wait, unassigned, for the Sales Manager. On: the system assigns them."""
    if not _can_configure(request.user):
        raise PermissionDenied
    from apps.sales.models import SalesSettings
    from apps.system.services import audit

    cfg = SalesSettings.get()
    cfg.auto_assign_new = not cfg.auto_assign_new
    cfg.save(update_fields=["auto_assign_new"])
    audit(request.user, "routing.toggled", cfg, "Automatic lead assignment " + ("ON" if cfg.auto_assign_new else "OFF"), request, label="Lead routing")
    messages.success(request, "Automatic lead assignment is ON. New leads are given to agents by the system." if cfg.auto_assign_new
                     else "Automatic lead assignment is OFF. New leads wait in Assign Leads until the Sales Manager gives them out.")
    nxt = request.POST.get("next") or ""
    return redirect(nxt if nxt.startswith("/ict/") else "ict:dashboard")


@require_POST
@module_required("ict")
def ai_choice(request):
    if not _can_configure(request.user):
        raise PermissionDenied
    from apps.system import services as sys_services
    from apps.system.models import IntegrationCredential

    prefer = "openai" if request.POST.get("prefer") == "openai" else "claude"
    row, _ = IntegrationCredential.objects.get_or_create(provider="ai")
    cur = row.values
    cur["prefer"] = prefer
    row.payload = json.dumps(cur)
    row.updated_by, row.updated_at = request.user, timezone.now()
    row.save()
    cfg = BotSettings.get()
    cfg.ai_fallback = bool(request.POST.get("ai_fallback"))
    cfg.save(update_fields=["ai_fallback"])
    sys_services.audit(request.user, "credential.saved", row, f"Preferred AI: {prefer}", request, label="Which AI")
    messages.success(request, "AI choice saved. It takes effect on the next message.")
    return redirect("ict:settings")


@module_required("ict")
def dashboard(request):
    cfg = BotSettings.get()
    from datetime import timedelta

    since = timezone.now() - timedelta(days=14)
    convs = BotConversation.objects.filter(started_at__gte=since)
    n = convs.count()
    linked = convs.filter(lead_captured=True).count()
    from apps.sales.models import SalesSettings

    return render(request, "ict/dashboard.html", {
        "routing_on": SalesSettings.get().auto_assign_new, "cfg": cfg, "ai_ready": ai.configured(), "n": n, "linked": linked, "taught": KnowledgeEntry.objects.filter(is_active=True).count(), "rate": round(100 * linked / n, 1) if n else 0,
        "recent": BotConversation.objects.select_related("client", "enquiry")[:8], "can_configure": _can_configure(request.user),
        "checklist": whatsapp.go_live_checklist()})


@module_required("ict")
def work_log(request):
    from datetime import date as _date

    from .models import WorkLogEntry

    emp = getattr(request.user, "employee", None)
    if request.method == "POST":
        text = (request.POST.get("description") or "").strip()[:300]
        if emp is None:
            messages.error(request, "Your login is not linked to a staff record, so the entry could not be saved.")
        elif not text:
            messages.error(request, "Say what you did.")
        else:
            def _t(v):
                try:
                    return datetime.strptime(v, "%H:%M").time()
                except (TypeError, ValueError):
                    return None

            try:
                day = _date.fromisoformat(request.POST.get("date") or "") 
            except ValueError:
                day = timezone.localdate()
            WorkLogEntry.objects.create(employee=emp, date=day, description=text, started_at=_t(request.POST.get("started_at")), ended_at=_t(request.POST.get("ended_at")))
            messages.success(request, "Work log entry added.")
        return redirect("ict:work_log")
    qs = WorkLogEntry.objects.select_related("employee")
    if not (request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO)):
        qs = qs.filter(employee=emp) if emp else qs.none()
    return render(request, "ict/work_log.html", {"entries": qs[:100], "today": timezone.localdate()})


@require_POST
@module_required("ict")
def work_log_delete(request, pk):
    from .models import WorkLogEntry

    e = get_object_or_404(WorkLogEntry, pk=pk)
    if not (request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO) or e.employee == getattr(request.user, "employee", None)):
        raise PermissionDenied
    e.delete()
    messages.success(request, "Entry removed.")
    return redirect("ict:work_log")


@csrf_exempt
def whatsapp_webhook(request):
    """The WhatsApp callback URL shown in ICT → WhatsApp. GET = Meta's verification handshake; POST = messages and delivery receipts.

    A delivery is accepted only with a valid X-Hub-Signature-256 when an app secret is saved (WhatsApp's, or the Meta app's)."""
    from . import meta

    if request.method == "GET":
        tokens = [t for t in (get_credential("whatsapp", "verify_token"), get_credential("meta", "verify_token")) if t]
        given = request.GET.get("hub.verify_token", "")
        if request.GET.get("hub.mode") == "subscribe" and any(hmac.compare_digest(given, t) for t in tokens):
            return HttpResponse(request.GET.get("hub.challenge", ""))
        return HttpResponse(status=403)
    if request.method != "POST":
        return HttpResponse(status=405)
    secrets_ = [x for x in (get_credential("whatsapp", "app_secret"), get_credential("meta", "app_secret")) if x]
    if secrets_:
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not any(hmac.compare_digest("sha256=" + hmac.new(x.encode(), request.body, hashlib.sha256).hexdigest(), sig) for x in secrets_):
            return HttpResponse(status=401)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    data.setdefault("object", "whatsapp_business_account")
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            change.setdefault("field", "messages")
    try:
        meta.receive(data)
    except Exception:  # never fail the webhook (Meta would retry endlessly)
        import logging

        logging.getLogger(__name__).exception("whatsapp webhook failed")
    return HttpResponse("ok")


@login_required
@require_POST
def assistant_ask(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "bad request"}, status=400)
    q = (data.get("question") or "").strip()
    if not q:
        return JsonResponse({"error": "Ask a question."}, status=400)
    if not ai.configured():
        return JsonResponse({"answer": "The assistant isn't switched on yet. Ask your administrator to add the AI key under Integration Keys."})
    try:
        return JsonResponse({"answer": assistant.ask(request.user, q, data.get("history"))})
    except ai.AIUnavailable as e:
        return JsonResponse({"answer": f"The assistant is unavailable right now ({e}). Try again shortly."})



# ------------------------------------------------------------------ bulk SMS and e-mail
def _can_send(user):
    return user.is_superuser or user.has_role(R.ADMIN, R.COO, R.ICT_OFFICER)


def _spec_from_post(post):
    return {"clients": {"enabled": bool(post.get("clients_enabled")), "sources": post.getlist("client_sources"), "stages": post.getlist("client_stages"),
                        "market": post.get("client_market", "")},
            "cards": {"enabled": bool(post.get("cards_enabled")), "categories": post.getlist("card_categories")},
            "manual": post.get("manual", "")}


@module_required("ict")
def broadcasts(request):
    from django.db.models import Sum

    from . import bulk
    from .models import Broadcast, BroadcastRecipient

    ch = request.GET.get("channel", "")
    qs = Broadcast.objects.select_related("created_by")
    if ch in ("sms", "email"):
        qs = qs.filter(channel=ch)
    month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    sent = BroadcastRecipient.objects.filter(status="sent", sent_at__gte=month)
    items = [{"b": b, "c": b.counts()} for b in qs[:60]]
    return render(request, "ict/broadcasts.html", {"items": items, "channel": ch, "can_send": _can_send(request.user), "sms_live": bulk.sms_live(), "email_live": bulk.email_live(),
                                                   "sms_month": sent.filter(broadcast__channel="sms").count(), "email_month": sent.filter(broadcast__channel="email").count(),
                                                   "failed_month": BroadcastRecipient.objects.filter(status="failed", sent_at__gte=month).count()})


@module_required("ict")
def broadcast_form(request, pk=None):
    from . import bulk
    from .models import Broadcast

    if not _can_send(request.user):
        raise PermissionDenied
    b = get_object_or_404(Broadcast, pk=pk) if pk else Broadcast(channel=request.GET.get("channel") if request.GET.get("channel") in ("sms", "email") else "sms", created_by=request.user)
    if b.pk and b.status not in (Broadcast.DRAFT, Broadcast.SCHEDULED):
        messages.info(request, "Only drafts and scheduled broadcasts can be edited.")
        return redirect("ict:broadcast", pk=b.pk)
    if request.method == "POST":
        b.name = request.POST.get("name", "").strip()[:120] or ("Bulk SMS" if b.channel == "sms" else "Bulk e-mail") + f" {timezone.localdate():%d %b}"
        b.subject, b.body, b.audience = request.POST.get("subject", "")[:200], request.POST.get("body", ""), _spec_from_post(request.POST)
        act = request.POST.get("action", "draft")
        if act == "test":
            b.save()
            try:
                messages.success(request, bulk.send_test(b.channel, request.POST.get("test_to", "").strip(), b.subject, b.body, request.build_absolute_uri("/").rstrip("/")))
            except (ValueError, bulk.ProviderError) as e:
                messages.error(request, str(e))
            return redirect("ict:broadcast_edit", pk=b.pk)
        if act == "draft":
            b.status = Broadcast.DRAFT
            b.save()
            messages.success(request, "Draft saved.")
            return redirect("ict:broadcast", pk=b.pk)
        when = None
        if request.POST.get("when") == "later":
            when = parse_local(request.POST.get("scheduled_at", ""))
            if when is None:
                messages.error(request, "Choose when it should go out.")
                b.save()
                return redirect("ict:broadcast_edit", pk=b.pk)
        b.save()
        try:
            n = bulk.start(b, when)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("ict:broadcast_edit", pk=b.pk)
        if b.status == Broadcast.SENDING:
            bulk.process(b, request.build_absolute_uri("/").rstrip("/"))      # first batch right away; the job carries on
        messages.success(request, f"{'Scheduled' if b.status == Broadcast.SCHEDULED else 'Sending'} to {n} recipient{'s' if n != 1 else ''}" + ("" if b.live else " (mock mode: nothing leaves the system until keys are added)") + ".")
        return redirect("ict:broadcast", pk=b.pk)
    return render(request, "ict/broadcast_form.html", {"b": b, "opts": bulk.audience_options(), "spec": b.audience or {}, "live": bulk.is_live(b.channel),
                                                       "when": timezone.localtime(b.scheduled_at).strftime("%Y-%m-%dT%H:%M") if b.scheduled_at else ""})


@require_POST
@module_required("ict")
def broadcast_preview(request):
    from . import bulk

    ch = "email" if request.POST.get("channel") == "email" else "sms"
    recs, skipped = bulk.build_audience(_spec_from_post(request.POST), ch)
    return JsonResponse({"count": len(recs), "skipped": len(skipped), "sample": [r["name"] or r["phone"] or r["email"] for r in recs[:6]],
                         "reasons": sorted({why for _n, why in skipped})[:4]})


@module_required("ict")
def broadcast_detail(request, pk):
    from . import bulk
    from .models import Broadcast

    b = get_object_or_404(Broadcast, pk=pk)
    if request.method == "POST":
        if not _can_send(request.user):
            raise PermissionDenied
        act = request.POST.get("action")
        if act == "process":
            bulk.process(b, request.build_absolute_uri("/").rstrip("/"), limit=500)
            messages.success(request, "Sent the next batch.")
        elif act == "cancel":
            bulk.cancel(b)
            messages.success(request, "Cancelled. Messages already sent can't be recalled.")
        elif act == "retry":
            bulk.retry_failed(b)
            bulk.process(b, request.build_absolute_uri("/").rstrip("/"))
            messages.success(request, "Retrying the failed ones.")
        elif act == "duplicate":
            n = Broadcast.objects.create(channel=b.channel, name=f"{b.name} (copy)", subject=b.subject, body=b.body, audience=b.audience, created_by=request.user)
            return redirect("ict:broadcast_edit", pk=n.pk)
        return redirect("ict:broadcast", pk=b.pk)
    status = request.GET.get("status", "")
    rec = b.recipients.all()
    if status:
        rec = rec.filter(status=status)
    return render(request, "ict/broadcast_detail.html", {"b": b, "c": b.counts(), "recipients": rec[:300], "status": status, "can_send": _can_send(request.user),
                                                         "segments": bulk.sms_segments(b.body) if b.channel == "sms" else None})


def unsubscribe(request, token):
    """Public page behind the link at the bottom of every bulk e-mail."""
    from apps.contacts.models import Client
    from apps.frontdesk.models import BusinessCard

    from . import bulk
    from .models import OptOut

    data = bulk.read_unsubscribe(token)
    if data is None:
        return render(request, "ict/unsubscribe.html", {"bad": True}, status=400)
    kind, value = data
    done = False
    if request.method == "POST":
        OptOut.objects.get_or_create(kind=kind, value=value)
        if kind == "email":
            Client.objects.filter(email__iexact=value).update(marketing_opt_out=True)
            BusinessCard.objects.filter(email__iexact=value).update(marketing_opt_out=True)
        done = True
    return render(request, "ict/unsubscribe.html", {"value": value, "done": done})



# ------------------------------------------------------------------ website form + Meta webhooks
@csrf_exempt
def website_hook(request):
    from .webhooks import receive_website

    return receive_website(request)


@csrf_exempt
def meta_hook(request):
    """GET: Meta's verification handshake. POST: signed deliveries (lead forms, WhatsApp, Messenger, Instagram)."""
    from . import meta

    if request.method == "GET":
        challenge = meta.verify_challenge(request.GET)
        return HttpResponse(challenge) if challenge is not None else HttpResponse(status=403)
    if request.method != "POST":
        return HttpResponse(status=405)
    if not meta.signature_ok(request.body, request.headers.get("X-Hub-Signature-256", "")):
        return HttpResponse(status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    try:
        meta.receive(payload)
    except Exception:  # always answer 200 once the signature is good, or Meta retries the same delivery for days
        import logging

        logging.getLogger(__name__).exception("meta webhook failed")
    return HttpResponse("ok")


@module_required("ict")
def webhooks_page(request):
    import secrets

    from apps.system.services import get_credential, save_credentials

    from . import meta, webhooks
    from .models import MetaEvent, WebhookDelivery

    can = _can_configure(request.user)
    if request.method == "POST":
        if not can:
            raise PermissionDenied
        act = request.POST.get("action")
        if act == "rotate":
            webhooks.rotate_token(request.user, request)
            messages.success(request, "New website token created. Update the token in your website forms; the old one stopped working.")
        elif act == "origins":
            save_credentials("webhooks", {"allowed_origins": request.POST.get("allowed_origins", "").strip()}, request.user, request)
            messages.success(request, "Allowed origins saved.")
        elif act == "test_website":
            from django.test import RequestFactory

            rf = RequestFactory().post("/webhooks/lead/", {"name": "Website Test Lead", "phone": "0700123458", "email": "web.test@example.com", "message": "Test from the Webhooks page", "_test": "1",
                                                           "token": webhooks.website_token()})
            resp = webhooks.receive_website(rf)
            messages.success(request, "Test lead sent through the website webhook. Look for “(Test lead)” in the Front Desk waiting list." if resp.status_code == 201 else f"The test failed ({resp.status_code}).")
        elif act in ("test_leadgen", "test_whatsapp"):
            meta.simulate("leadgen" if act == "test_leadgen" else "whatsapp")
            messages.success(request, "Sample delivered through the Meta path. Look for “(Test lead)” in the Front Desk waiting list.")
        elif act == "meta_keys":
            vals = {k: request.POST.get(k, "").strip() for k in ("verify_token", "app_secret", "access_token")}
            save_credentials("meta", vals, request.user, request)
            messages.success(request, "Meta settings saved. They apply to the next delivery with no restart.")
        elif act == "gen_verify":
            save_credentials("meta", {"verify_token": secrets.token_urlsafe(16)}, request.user, request)
            messages.success(request, "Verify token generated. Paste it into the Meta app's webhook settings.")
        elif act == "retry":
            messages.success(request, f"Retried: {meta.retry_failed()} recovered.")
        return redirect("ict:webhooks")
    base = request.build_absolute_uri("/").rstrip("/")
    tok = webhooks.website_token()
    return render(request, "ict/webhooks.html", {
        "can": can, "token": tok, "snip": webhooks.snippets(base, tok), "origins": get_credential("webhooks", "allowed_origins"), "meta_url": f"{base}/webhooks/meta/",
        "meta": meta.status(), "verify_token": get_credential("meta", "verify_token"), "deliveries": WebhookDelivery.objects.select_related("enquiry")[:40],
        "events": MetaEvent.objects.select_related("enquiry")[:15], "summary": meta.summary(), "website_count": WebhookDelivery.objects.filter(source="website", ok=True, is_test=False).count()})


@module_required("ict")
def backups_page(request):
    from . import backups
    from .models import JsonBackupRun, JsonBackupSettings

    cfg = JsonBackupSettings.get()
    can = _can_configure(request.user)
    if request.method == "POST":
        if not can:
            raise PermissionDenied
        if request.POST.get("action") == "now":
            run = backups.create_backup()
            messages.success(request, f"Backup ready: {run.object_count} records, {run.size / 1_048_576:.1f} MB.")
        else:
            if request.POST.get("frequency") in ("daily", "weekly", "monthly"):
                cfg.frequency = request.POST["frequency"]
            try:
                cfg.keep = max(1, min(int(request.POST.get("keep", cfg.keep)), 60))
            except ValueError:
                pass
            cfg.save()
            messages.success(request, "Backup schedule saved.")
        return redirect("ict:backups")
    return render(request, "ict/backups.html", {"cfg": cfg, "runs": JsonBackupRun.objects.all()[:30], "can": can, "due": backups.is_due()})


@module_required("ict")
def backup_download(request, pk):
    from django.http import FileResponse

    from . import backups
    from .models import JsonBackupRun

    run = get_object_or_404(JsonBackupRun, pk=pk)
    backups.mark_downloaded(run, request.user)
    return FileResponse(run.file.open("rb"), as_attachment=True, filename=run.file.name.rsplit("/", 1)[-1], content_type="application/json")


# ------------------------------------------------------------------------------------------------ bot training
@module_required("ict")
def training(request):
    """Teach the bot: Q&A the team approves, answers the bot uses word for word, and the questions customers ask that it can't answer yet."""
    from . import knowledge

    if request.method == "POST":
        if not _can_configure(request.user):
            raise PermissionDenied
        q, a = (request.POST.get("question") or "").strip()[:240], (request.POST.get("answer") or "").strip()[:900]
        if not q or not a:
            messages.error(request, "Add both the question and the answer.")
        else:
            pk = request.POST.get("id", "")
            fields = {"question": q, "answer": a, "keywords": (request.POST.get("keywords") or "")[:240], "category": (request.POST.get("category") or "")[:40]}
            if pk.isdigit():
                KnowledgeEntry.objects.filter(pk=pk).update(**fields)
                messages.success(request, "Updated.")
            else:
                KnowledgeEntry.objects.create(created_by=request.user, **fields)
                messages.success(request, "Taught. The bot will use this answer.")
        return redirect("ict:training")
    cat = request.GET.get("cat", "")
    qs = KnowledgeEntry.objects.all()
    if cat:
        qs = qs.filter(category=cat)
    qtext = (request.GET.get("q") or "").strip()
    if qtext:
        qs = qs.filter(Q(question__icontains=qtext) | Q(answer__icontains=qtext) | Q(keywords__icontains=qtext))
    edit = KnowledgeEntry.objects.filter(pk=request.GET.get("edit")).first() if (request.GET.get("edit") or "").isdigit() else None
    return render(request, "ict/training.html", {
        "entries": qs, "cats": list(KnowledgeEntry.objects.exclude(category="").order_by().values_list("category", flat=True).distinct()), "cat": cat, "q": qtext,
        "total": KnowledgeEntry.objects.count(), "active": KnowledgeEntry.objects.filter(is_active=True).count(),
        "suggested": knowledge.unanswered(), "edit": edit, "can_configure": _can_configure(request.user),
        "prefill": request.GET.get("teach", ""), "ai_ready": ai.configured()})


@require_POST
@module_required("ict")
def training_action(request, pk):
    if not _can_configure(request.user):
        raise PermissionDenied
    e = get_object_or_404(KnowledgeEntry, pk=pk)
    if request.POST.get("action") == "delete":
        e.delete()
        messages.success(request, "Removed.")
    else:
        e.is_active = not e.is_active
        e.save(update_fields=["is_active"])
    return redirect("ict:training")


@require_POST
@module_required("ict")
def training_seed(request):
    if not _can_configure(request.user):
        raise PermissionDenied
    from . import knowledge

    n = knowledge.seed_from_catalogue(request.user)
    messages.success(request, f"Added {n} answer{'s' if n != 1 else ''} from the package catalogue." if n else "The catalogue is already covered.")
    return redirect("ict:training")


@module_required("ict")
def training_try(request):
    """Try a question as a customer would ask it: what the bot would say, and which taught answers it used."""
    from . import knowledge

    text = (request.GET.get("q") or "").strip()[:300]
    if not text:
        return JsonResponse({"error": "Type a question."}, status=400)
    hits = knowledge.search(text, 3, 0.15)
    out = {"matches": [{"question": e.question, "answer": e.answer, "score": round(s, 2)} for s, e in hits], "mode": "none", "reply": ""}
    if hits and hits[0][0] >= 0.8:
        out.update(mode="taught", reply=hits[0][1].answer)
    elif ai.configured():
        try:
            out.update(mode="ai", reply=ai.text_of(ai.complete(services.system_prompt(BotSettings.get(), text), [{"role": "user", "content": text}])))
        except ai.AIUnavailable as e:
            out.update(mode="error", reply=str(e))
    elif hits and hits[0][0] >= 0.5:
        out.update(mode="taught", reply=hits[0][1].answer)
    return JsonResponse(out)
