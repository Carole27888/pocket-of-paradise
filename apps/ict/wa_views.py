"""ICT → WhatsApp and ICT → AI: everything needed to go live is entered here (no code, no environment variables)."""
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required
from apps.core.forms import StyledModelForm
from apps.sales.models import TravelReminder
from apps.system import services as sys_services
from apps.system.models import IntegrationCredential
from apps.system.services import get_credential, save_credentials

from . import ai, whatsapp
from .models import AICall, ExecChat, MessagingSettings, WhatsAppTemplate
from .views import _can_configure

CLAUDE_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
OPENAI_MODELS = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"]
VARIABLE_HELP = [("name", "the person's first name"), ("title", "what happened, e.g. New enquiry ENQ-00123"), ("body", "the details line"),
                 ("link", "the link to open it"), ("client", "the client's name"), ("date", "the travel date"), ("trip", "the trip name"),
                 ("needs", "what the client must have ready")]


def _need(user):
    if not _can_configure(user):
        raise PermissionDenied


def _base(request) -> str:
    return request.build_absolute_uri("/").rstrip("/")


def _is_local(request) -> bool:
    """True when this address can't be reached by Meta (a LAN IP, localhost, plain http)."""
    import ipaddress

    host = request.get_host().split(":")[0]
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",) or "." not in host or not request.is_secure()


def _fields(provider):
    out = []
    for name, label, secret in sys_services.PROVIDERS[provider][1]:
        val = get_credential(provider, name)
        out.append({"name": name, "label": label, "secret": secret, "has": bool(val), "display": sys_services.mask(val) if secret else val})
    return out


class MessagingForm(StyledModelForm):
    class Meta:
        model = MessagingSettings
        fields = ["notifications_enabled", "public_base_url", "max_retries", "travel_reminder_days", "reminder_hour", "remind_agents", "remind_clients"]
        labels = {"notifications_enabled": "Send staff alerts on WhatsApp / e-mail", "public_base_url": "System address used in links",
                  "travel_reminder_days": "Remind this many days before departure", "remind_agents": "Remind the agent", "remind_clients": "Remind the client"}

    def clean_travel_reminder_days(self):
        v = self.cleaned_data["travel_reminder_days"]
        parts = [p for p in v.replace(" ", "").split(",") if p]
        if not parts or not all(p.isdigit() and 0 <= int(p) <= 60 for p in parts):
            raise forms.ValidationError("Use whole numbers of days between 0 and 60, separated by commas, e.g. 5,1")
        return ",".join(sorted(set(parts), key=lambda x: -int(x)))

    def clean_reminder_hour(self):
        h = self.cleaned_data["reminder_hour"]
        if not 0 <= h <= 23:
            raise forms.ValidationError("An hour from 0 to 23.")
        return h


class TemplateForm(StyledModelForm):
    full_width_fields = ("body", "variables")

    class Meta:
        model = WhatsAppTemplate
        fields = ["name", "language", "category", "purpose", "header", "body", "footer", "variables"]
        widgets = {"body": forms.Textarea(attrs={"rows": 5})}

    def clean_name(self):
        import re

        n = self.cleaned_data["name"].strip().lower().replace("-", "_")
        if not re.fullmatch(r"[a-z0-9_]{1,80}", n):
            raise forms.ValidationError("Lower-case letters, numbers and underscores only.")
        return n

    def clean(self):
        d = super().clean()
        body = (d.get("body") or "").strip()
        import re

        if re.match(r"^\s*\{\{\d+\}\}", body) or re.search(r"\{\{\d+\}\}\s*[.!?]?\s*$", body):
            self.add_error("body", "Meta rejects templates that start or end with a variable. Add words before the first and after the last.")
        nums = sorted({int(x) for x in re.findall(r"\{\{(\d+)\}\}", body)})
        if nums and nums != list(range(1, len(nums) + 1)):
            self.add_error("body", "Number the variables {{1}}, {{2}}, {{3}}… with no gaps.")
        names = [v.strip() for v in (d.get("variables") or "").split(",") if v.strip()]
        if nums and names and len(names) != len(nums):
            self.add_error("variables", f"The body has {len(nums)} variable(s); list exactly {len(nums)} name(s).")
        return d


# ================================================================================================ WhatsApp
@module_required("ict")
def whatsapp_page(request):
    _need(request.user)
    cfg = MessagingSettings.get()
    if request.method == "POST":
        act = request.POST.get("action")
        if act == "keys":
            vals = {k: request.POST.get(k, "") for k in ("access_token", "phone_number_id", "waba_id", "app_secret", "verify_token", "api_version")}
            save_credentials("whatsapp", vals, request.user, request)
            if not cfg.public_base_url:
                cfg.public_base_url = _base(request)
                cfg.save(update_fields=["public_base_url"])
            if whatsapp.configured() and not WhatsAppTemplate.objects.exists():
                whatsapp.ensure_default_templates()
            messages.success(request, "WhatsApp details saved and encrypted. They are in use from the next message." + (" Now press Test connection." if whatsapp.configured() else ""))
        elif act == "gen_verify":
            save_credentials("whatsapp", {"verify_token": secrets.token_urlsafe(18)}, request.user, request)
            messages.success(request, "Verify token created. Copy it into Meta together with the callback URL.")
        elif act == "test":
            ok, msg = sys_services.test_credentials("whatsapp", request.user, request)
            request.session["wa_checks"] = whatsapp.check_connection() if not getattr(settings, "INTEGRATIONS_OFFLINE", False) else []
            (messages.success if ok else messages.error)(request, "Connection works." if ok else "Something needs fixing: see the checks below.")
        elif act == "subscribe":
            ok, msg = whatsapp.subscribe_app()
            (messages.success if ok else messages.error)(request, msg)
        elif act == "send_test":
            from apps.integrations.messaging import send_message

            to = request.POST.get("to", "").strip()
            text = (request.POST.get("text") or "").strip() or "Test message from the Pocket of Paradise system. WhatsApp is connected."
            if not to:
                messages.error(request, "Enter the number to send the test to.")
            else:
                force = None
                if whatsapp.configured() and not whatsapp.window_open(to) and not whatsapp._template_for("notification"):
                    force = "hello_world"
                if force:                               # every WhatsApp account has Meta's own hello_world template, so a first test always works
                    try:
                        mid = whatsapp.send_template(to, WhatsAppTemplate(name="hello_world", language="en_US", body="Hello World"), [])
                        messages.success(request, f"Sent Meta's “hello_world” template to {to} (id {mid[:18]}…). Free text needs the person to message the number first.")
                    except whatsapp.WhatsAppError as e:
                        messages.error(request, f"Not sent: {e}")
                else:
                    m = send_message("whatsapp", to, text, purpose="notification", related="test", user=request.user,
                                     fields={"name": request.user.full_name.split()[0], "title": "Test message", "body": text, "link": _base(request) + "/"})
                    if m.status == "sent":
                        messages.success(request, f"Test sent to {m.to}" + (f" with the “{m.template.split(':')[0]}” template." if m.template else ".") +
                                         (" (Mock mode: WhatsApp isn't connected yet, so nothing really left.)" if m.provider == "mock" else ""))
                    else:
                        messages.error(request, f"Not sent: {m.error}")
        elif act == "settings":
            form = MessagingForm(request.POST, instance=cfg)
            if form.is_valid():
                form.save()
                messages.success(request, "Saved.")
            else:
                messages.error(request, " ".join(f"{form.fields[k].label}: {' '.join(v)}" for k, v in form.errors.items()))
        elif act == "defaults":
            n = whatsapp.ensure_default_templates()
            messages.success(request, f"{n} standard template{'s' if n != 1 else ''} added as drafts. Press “Submit all drafts to Meta”." if n else "The standard templates are already here.")
        elif act == "submit_all":
            done, errs = 0, []
            for t in WhatsAppTemplate.objects.filter(status__in=[WhatsAppTemplate.DRAFT, WhatsAppTemplate.REJECTED]):
                ok, msg = whatsapp.submit_template(t)
                done += ok
                if not ok:
                    errs.append(f"{t.name}: {msg}")
            if done:
                messages.success(request, f"{done} template{'s' if done != 1 else ''} sent to Meta for approval.")
            for e in errs[:4]:
                messages.error(request, e)
        elif act == "sync":
            ok, msg = whatsapp.sync_templates()
            (messages.success if ok else messages.error)(request, msg)
        return redirect("ict:whatsapp")
    from apps.accounts.models import User
    from apps.notifications.models import OutboundMessage

    since = timezone.now() - timedelta(days=7)
    wa = OutboundMessage.objects.filter(channel="whatsapp", created_at__gte=since)
    counts = dict(wa.values_list("status").annotate(n=Count("pk")))
    staff = list(User.objects.filter(is_active=True).select_related("employee").prefetch_related("groups").order_by("full_name"))
    missing = [u for u in staff if u.wants_whatsapp and not u.whatsapp_target]
    c = whatsapp.creds()
    return render(request, "ict/whatsapp.html", {
        "cfg": cfg, "form": MessagingForm(instance=cfg), "fields": _fields("whatsapp"), "live": whatsapp.configured(), "creds": c,
        "verify_token": c["verify_token"], "webhook_url": _base(request) + "/ict/webhooks/whatsapp/", "local_url": _is_local(request), "checks": request.session.pop("wa_checks", None),
        "templates": WhatsAppTemplate.objects.all(), "approved": WhatsAppTemplate.objects.filter(status=WhatsAppTemplate.APPROVED).values_list("purpose", flat=True),
        "purposes": WhatsAppTemplate.PURPOSES, "variable_help": VARIABLE_HELP, "counts": counts, "week_total": sum(counts.values()),
        "missing": missing, "staff_count": len(staff), "row": IntegrationCredential.objects.filter(provider="whatsapp").first(),
        "reminders": TravelReminder.objects.select_related("booking__client")[:8]})


@module_required("ict")
def template_form(request, pk=None):
    _need(request.user)
    obj = get_object_or_404(WhatsAppTemplate, pk=pk) if pk else None
    form = TemplateForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        t = form.save(commit=False)
        if obj and obj.status != WhatsAppTemplate.DRAFT and form.has_changed():
            t.status, t.meta_id = WhatsAppTemplate.DRAFT, ""          # a changed template must be approved again
        t.save()
        messages.success(request, f"Template “{t.name}” saved as a draft. Submit it to Meta for approval." if t.status == WhatsAppTemplate.DRAFT else "Template saved.")
        return redirect("ict:whatsapp")
    return render(request, "ict/whatsapp_template.html", {"form": form, "obj": obj, "variable_help": VARIABLE_HELP})


@require_POST
@module_required("ict")
def template_action(request, pk):
    _need(request.user)
    t = get_object_or_404(WhatsAppTemplate, pk=pk)
    act = request.POST.get("action")
    if act == "submit":
        ok, msg = whatsapp.submit_template(t)
    elif act == "delete":
        ok, msg = whatsapp.delete_template(t)
    elif act == "approve_local":              # for a template already approved in Meta's dashboard under the same name
        t.status = WhatsAppTemplate.APPROVED
        t.save(update_fields=["status"])
        ok, msg = True, "Marked approved. Use “Check status with Meta” to confirm."
    else:
        ok, msg = False, "Unknown action."
    (messages.success if ok else messages.error)(request, msg)
    return redirect("ict:whatsapp")


@module_required("ict")
def message_log(request):
    _need(request.user)
    from apps.integrations.messaging import attempt
    from apps.notifications.models import OutboundMessage

    if request.method == "POST":
        if request.POST.get("action") == "retry_all":
            n = 0
            for m in OutboundMessage.objects.filter(channel="whatsapp", status="failed", created_at__gte=timezone.now() - timedelta(days=3))[:100]:
                attempt(m)
                n += m.status == "sent"
            messages.success(request, f"{n} message{'s' if n != 1 else ''} sent on retry.")
        else:
            m = get_object_or_404(OutboundMessage, pk=request.POST.get("pk"))
            attempt(m)
            (messages.success if m.status == "sent" else messages.error)(request, "Sent." if m.status == "sent" else f"Still failing: {m.error}")
        return redirect(request.get_full_path())
    qs = OutboundMessage.objects.select_related("user")
    ch, st, q = request.GET.get("channel", "whatsapp"), request.GET.get("status", ""), request.GET.get("q", "").strip()
    if ch:
        qs = qs.filter(channel=ch)
    if st:
        qs = qs.filter(status=st)
    if q:
        qs = qs.filter(Q(to__icontains=q) | Q(body__icontains=q) | Q(user__full_name__icontains=q) | Q(purpose__icontains=q))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "ict/message_log.html", {"page": page, "channel": ch, "status": st, "q": q,
                                                    "statuses": OutboundMessage.STATUSES, "channels": OutboundMessage.CHANNELS})


# ================================================================================================ AI
@module_required("ict")
def ai_page(request):
    _need(request.user)
    cfg = MessagingSettings.get()
    if request.method == "POST":
        act = request.POST.get("action")
        if act in ("claude", "openai"):
            prov = "ai" if act == "claude" else "openai"
            save_credentials(prov, {"api_key": request.POST.get("api_key", ""), "model": request.POST.get("model", "")}, request.user, request)
            if request.POST.get("make_default"):
                _set_prefer(request, act)
            messages.success(request, f"{'Claude' if act == 'claude' else 'OpenAI'} saved. It is used from the next question.")
        elif act == "test":
            prov = "ai" if request.POST.get("provider") == "claude" else "openai"
            ok, msg = sys_services.test_credentials(prov, request.user, request)
            (messages.success if ok else messages.error)(request, msg)
        elif act == "prefer":
            _set_prefer(request, "openai" if request.POST.get("prefer") == "openai" else "claude")
            from .models import BotSettings

            b = BotSettings.get()
            b.ai_fallback = bool(request.POST.get("ai_fallback"))
            b.save(update_fields=["ai_fallback"])
            messages.success(request, "AI provider saved. It takes effect on the next question.")
        elif act == "remove":
            sys_services.delete_credentials("ai" if request.POST.get("provider") == "claude" else "openai", request.user, request)
            messages.success(request, "Key removed.")
        elif act == "assistant":
            cfg.exec_assistant_enabled = bool(request.POST.get("exec_assistant_enabled"))
            cfg.exec_roles = ",".join(r for r in request.POST.getlist("exec_roles") if r)[:120] or "Director,COO"
            cfg.save(update_fields=["exec_assistant_enabled", "exec_roles"])
            messages.success(request, "Business assistant settings saved.")
        elif act == "prices":
            from decimal import Decimal, InvalidOperation

            try:
                for f in ("claude_input_price", "claude_output_price", "openai_input_price", "openai_output_price"):
                    setattr(cfg, f, max(Decimal(request.POST.get(f) or "0"), Decimal("0")))
                cfg.save()
                messages.success(request, "Prices saved. Cost estimates use them from now on.")
            except InvalidOperation:
                messages.error(request, "Prices must be numbers, e.g. 3 or 0.15.")
        elif act == "ask":
            from . import exec_assistant

            q = (request.POST.get("question") or "").strip()
            if q:
                request.session["exec_try"] = {"q": q, "a": exec_assistant.answer(request.user, request.user.whatsapp_target or "web", q)}
        return redirect("ict:ai")
    from apps.accounts import roles as R
    from apps.accounts.models import User

    from .models import BotSettings

    days = 30
    since = timezone.now() - timedelta(days=days)
    calls = AICall.objects.filter(created_at__gte=since)
    by_feature = list(calls.values("feature", "provider").annotate(n=Count("pk"), tin=Sum("input_tokens"), tout=Sum("output_tokens"), cost=Sum("cost_usd"), failed=Count("pk", filter=Q(ok=False))).order_by("-cost"))
    total = calls.aggregate(n=Count("pk"), cost=Sum("cost_usd"), tin=Sum("input_tokens"), tout=Sum("output_tokens"))
    month_start = timezone.localdate().replace(day=1)
    month_cost = AICall.objects.filter(created_at__date__gte=month_start).aggregate(c=Sum("cost_usd"))["c"] or 0
    execs = [u for u in User.objects.filter(is_active=True).select_related("employee").prefetch_related("groups") if u.role_names.intersection(cfg.exec_role_names)]
    prefer = (get_credential("ai", "prefer") or "claude").lower()
    return render(request, "ict/ai.html", {
        "cfg": cfg, "claude": _fields("ai"), "openai": _fields("openai"), "has_claude": ai.has_claude(), "has_openai": ai.has_openai(),
        "active": ai.active_provider(), "prefer": "openai" if prefer.startswith("open") else "claude", "fallback": BotSettings.get().ai_fallback,
        "claude_model": get_credential("ai", "model") or settings.AI_MODEL, "openai_model": get_credential("openai", "model") or ai.OPENAI_DEFAULT_MODEL,
        "claude_models": CLAUDE_MODELS, "openai_models": OPENAI_MODELS, "by_feature": by_feature, "total": total, "days": days,
        "tokens_label": f"{total['tin'] or 0:,} / {total['tout'] or 0:,}", "cost_label": f"{total['cost'] or 0:,.2f}", "month_label": f"{month_cost:,.2f}",
        "chats": ExecChat.objects.select_related("user")[:40], "execs": execs, "all_roles": R.ALL_ROLES, "exec_roles": cfg.exec_role_names,
        "rows": {r.provider: r for r in IntegrationCredential.objects.filter(provider__in=["ai", "openai"])},
        "tried": request.session.pop("exec_try", None), "recent_errors": calls.filter(ok=False)[:5]})


def _set_prefer(request, prefer):
    import json

    from apps.system.models import IntegrationCredential

    row, _ = IntegrationCredential.objects.get_or_create(provider="ai")
    cur = row.values
    cur["prefer"] = prefer
    row.payload = json.dumps(cur)
    row.updated_by, row.updated_at = request.user, timezone.now()
    row.save()
    sys_services.audit(request.user, "credential.saved", row, f"Preferred AI: {prefer}", request, label="Which AI")
