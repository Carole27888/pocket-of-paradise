"""WhatsApp Business Cloud API: everything the system sends or asks of Meta goes through here.

Keys live in ICT → WhatsApp (encrypted, see apps.system.services.PROVIDERS["whatsapp"]). Once an access token and a phone number ID
are saved, every alert, reminder and bot reply goes out for real; before that, messages are recorded in mock mode.

Meta's rule: a business may send free text only within 24 hours of the person's last message to it. Outside that window only an
approved template may be sent. `send()` picks the right one: free text when the window is open, otherwise the approved template for
the kind of message (or the general staff-alert template), filled with the message's fields.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.utils import timezone

from apps.core.phone import normalise_phone
from apps.system.services import get_credential

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v21.0"
REENGAGE_ERRORS = {131047, 131026, 470}      # outside the 24-hour window / message undeliverable as free text


class WhatsAppError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ keys
def creds() -> dict:
    return {f: get_credential("whatsapp", f) for f in ("access_token", "phone_number_id", "waba_id", "app_secret", "verify_token", "api_version")}


def configured() -> bool:
    c = creds()
    return bool(c["access_token"] and c["phone_number_id"])


def version() -> str:
    v = (get_credential("whatsapp", "api_version") or DEFAULT_VERSION).strip()
    return v if v.startswith("v") else "v" + v


def _graph(method: str, path: str, payload: dict | None = None, params: dict | None = None) -> tuple[int, dict]:
    """One HTTP call to the Graph API. The only network code here, so tests replace it."""
    token = get_credential("whatsapp", "access_token")
    url = f"{GRAPH}/{version()}/{path.lstrip('/')}" + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"error": {"message": f"HTTP {e.code}"}}
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": {"message": f"Could not reach Meta: {getattr(e, 'reason', e)}"}}


def _error(status: int, data: dict) -> WhatsAppError:
    err = (data or {}).get("error") or {}
    msg = err.get("error_user_msg") or err.get("message") or f"HTTP {status}"
    details = (err.get("error_data") or {}).get("details")
    return WhatsAppError(f"{msg}{' — ' + details if details and details not in msg else ''} (code {err.get('code', status)})", err.get("code"))


# ------------------------------------------------------------------ the 24-hour window
def mark_inbound(phone: str):
    from .models import WhatsAppWindow

    phone = normalise_phone(phone)
    if phone:
        WhatsAppWindow.objects.update_or_create(phone=phone, defaults={"last_inbound_at": timezone.now()})


def window_open(phone: str) -> bool:
    from .models import WhatsAppWindow

    w = WhatsAppWindow.objects.filter(phone=normalise_phone(phone)).first()
    return bool(w and w.is_open)


# ------------------------------------------------------------------ templates
def _template_for(purpose: str):
    from .models import WhatsAppTemplate as T

    approved = T.objects.filter(status=T.APPROVED)
    return (approved.filter(purpose=purpose).first() if purpose else None) or approved.filter(purpose=T.NOTIFICATION).first()


def clean_param(value) -> str:
    """Meta refuses template values with new lines, tabs or more than four spaces in a row, and empty ones."""
    s = re.sub(r"[\r\n\t]+", " · ", str(value if value is not None else "")).strip()
    s = re.sub(r" {4,}", "   ", s)
    return (s or "-")[:900]


def params_for(tpl, fields: dict) -> list[str]:
    names = tpl.variable_names or ["name", "title", "body", "link"]
    vals = [clean_param(fields.get(n, "")) for n in names]
    n = tpl.variable_count
    return (vals + ["-"] * n)[:n]


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", normalise_phone(phone) or phone or "")


def send_text(to: str, body: str) -> str:
    status, data = _graph("POST", f"{get_credential('whatsapp', 'phone_number_id')}/messages", {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": _digits(to), "type": "text",
        "text": {"preview_url": True, "body": body[:4096]}})
    if status >= 400 or status == 0:
        raise _error(status, data)
    return ((data.get("messages") or [{}])[0]).get("id", "")


def send_template(to: str, tpl, params: list[str]) -> str:
    comp = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}] if params else []
    status, data = _graph("POST", f"{get_credential('whatsapp', 'phone_number_id')}/messages", {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": _digits(to), "type": "template",
        "template": {"name": tpl.name, "language": {"code": tpl.language}, "components": comp}})
    if status >= 400 or status == 0:
        raise _error(status, data)
    return ((data.get("messages") or [{}])[0]).get("id", "")


def send(to: str, body: str, *, purpose: str = "", fields: dict | None = None, force_template: bool = False) -> dict:
    """Send one message the way Meta allows. Returns {"id", "template", "rendered"}; raises WhatsAppError with a plain reason."""
    if not configured():
        raise WhatsAppError("WhatsApp is not set up: add the access token and phone number ID under ICT → WhatsApp.")
    if not force_template and window_open(to):
        try:
            return {"id": send_text(to, body), "template": "", "rendered": body}
        except WhatsAppError as e:
            if e.code not in REENGAGE_ERRORS:
                raise                                   # a real error; outside-window errors fall through to a template
    tpl = _template_for(purpose)
    if tpl is None:
        raise WhatsAppError("This person hasn't written to the WhatsApp number in the last 24 hours, so Meta only allows an approved template, "
                            "and none is approved yet. Create and submit the templates under ICT → WhatsApp → Templates.")
    params = params_for(tpl, {"body": body, **(fields or {})})
    return {"id": send_template(to, tpl, params), "template": f"{tpl.name}:{tpl.language}", "rendered": tpl.render(params)}


# ------------------------------------------------------------------ delivery receipts
STATUS_ORDER = {"queued": 0, "failed": 0, "sent": 1, "delivered": 2, "read": 3}


def apply_statuses(statuses: list) -> int:
    """Meta's sent / delivered / read / failed receipts update the message log. Returns how many messages changed."""
    from apps.notifications.models import OutboundMessage

    n = 0
    for s in statuses or []:
        msg = OutboundMessage.objects.filter(provider_id=s.get("id", "")).first() if s.get("id") else None
        if msg is None:
            continue
        new = s.get("status", "")
        when = timezone.now()
        try:
            when = timezone.datetime.fromtimestamp(int(s.get("timestamp")), tz=timezone.get_current_timezone())
        except (TypeError, ValueError):
            pass
        fields = []
        if new == "failed":
            err = (s.get("errors") or [{}])[0]
            code = err.get("code")
            msg.status, msg.error = "failed", f"{err.get('title') or err.get('message') or 'Meta could not deliver it'} (code {code})"[:300]
            msg.payload = {**msg.payload, "force_template": code in REENGAGE_ERRORS or msg.payload.get("force_template", False)}
            msg.next_retry_at = timezone.now() if code in REENGAGE_ERRORS and not msg.template else timezone.now() + timedelta(minutes=10)
            fields = ["status", "error", "payload", "next_retry_at"]
        elif new in ("sent", "delivered", "read") and STATUS_ORDER[new] >= STATUS_ORDER.get(msg.status, 0):
            msg.status = new
            fields = ["status"]
            if new in ("delivered", "read") and not msg.delivered_at:
                msg.delivered_at = when
                fields.append("delivered_at")
            if new == "read":
                msg.read_at = when
                fields.append("read_at")
        if fields:
            msg.save(update_fields=fields)
            n += 1
    return n


# ------------------------------------------------------------------ connection checks (ICT → WhatsApp → Test connection)
def check_connection() -> list[dict]:
    """Each thing that must be right, with what Meta said. Never raises."""
    c = creds()
    rows = []

    def row(label, ok, detail):
        rows.append({"label": label, "ok": ok, "detail": detail})

    if not c["access_token"] or not c["phone_number_id"]:
        row("Access token and phone number ID", False, "Both are required. Find them in Meta → WhatsApp → API Setup.")
        return rows
    status, data = _graph("GET", c["phone_number_id"], params={"fields": "display_phone_number,verified_name,quality_rating,code_verification_status"})
    if status == 200:
        row("Phone number", True, f"{data.get('display_phone_number', '?')} · {data.get('verified_name', '')} · quality {data.get('quality_rating', 'n/a')}")
    else:
        row("Phone number", False, str(_error(status, data)))
        return rows
    if c["waba_id"]:
        status, data = _graph("GET", c["waba_id"], params={"fields": "name,message_template_namespace"})
        row("Business account", status == 200, data.get("name", "") if status == 200 else str(_error(status, data)))
        status, data = _graph("GET", f"{c['waba_id']}/subscribed_apps")
        subscribed = status == 200 and bool(data.get("data"))
        row("Webhook subscription", subscribed, "This app receives the account's messages." if subscribed else
            "Not subscribed yet: press “Subscribe to messages” so replies and delivery receipts reach the system.")
    else:
        row("Business account ID", False, "Add the WhatsApp Business Account ID to manage templates and receive messages.")
    row("Webhook verify token", bool(c["verify_token"]), "Saved." if c["verify_token"] else "Generate one and paste it into Meta with the callback URL.")
    row("App secret", bool(c["app_secret"] or get_credential("meta", "app_secret")), "Incoming messages are checked against it." if (c["app_secret"] or get_credential("meta", "app_secret"))
        else "Recommended: with it, the system rejects anything not really sent by Meta.")
    return rows


def subscribe_app() -> tuple[bool, str]:
    waba = get_credential("whatsapp", "waba_id")
    if not waba:
        return False, "Add the WhatsApp Business Account ID first."
    status, data = _graph("POST", f"{waba}/subscribed_apps", {})
    return (True, "Subscribed. Messages and delivery receipts will now reach the webhook.") if status == 200 else (False, str(_error(status, data)))


# ------------------------------------------------------------------ template management
def _components(t) -> list:
    comps = []
    if t.header:
        comps.append({"type": "HEADER", "format": "TEXT", "text": t.header})
    body = {"type": "BODY", "text": t.body}
    n = t.variable_count
    if n:
        examples = {"name": "Sarah", "title": "New enquiry ENQ-00123", "body": "Jane Doe · 3-day Maasai Mara", "link": "https://example.com/l/123",
                    "client": "Jane Doe", "date": "Mon 12 Jan 2027", "trip": "3-Day Maasai Mara Safari", "needs": "passport, yellow fever card, balance of USD 450",
                    "agent": "Sarah Njeri"}
        names = (t.variable_names + ["body"] * n)[:n]
        body["example"] = {"body_text": [[examples.get(x, "Example") for x in names]]}
    comps.append(body)
    if t.footer:
        comps.append({"type": "FOOTER", "text": t.footer})
    return comps


def submit_template(t) -> tuple[bool, str]:
    waba = get_credential("whatsapp", "waba_id")
    if not waba:
        return False, "Add the WhatsApp Business Account ID first."
    status, data = _graph("POST", f"{waba}/message_templates", {"name": t.name, "language": t.language, "category": t.category, "components": _components(t)})
    if status >= 400 or status == 0:
        e = _error(status, data)
        t.rejected_reason = str(e)[:300]
        t.save(update_fields=["rejected_reason"])
        return False, str(e)
    t.meta_id, t.status, t.rejected_reason, t.synced_at = str(data.get("id", "")), (data.get("status") or "PENDING").lower(), "", timezone.now()
    if t.status not in dict(t.STATUSES):
        t.status = t.PENDING
    t.save(update_fields=["meta_id", "status", "rejected_reason", "synced_at"])
    return True, f"Sent to Meta for review ({t.get_status_display()}). Approval usually takes minutes, sometimes a day."


def sync_templates() -> tuple[bool, str]:
    """Bring every template's status (and any made directly in Meta) into the system."""
    from .models import WhatsAppTemplate as T

    waba = get_credential("whatsapp", "waba_id")
    if not waba:
        return False, "Add the WhatsApp Business Account ID first."
    status, data = _graph("GET", f"{waba}/message_templates", params={"limit": 200, "fields": "id,name,language,status,category,rejected_reason,components"})
    if status != 200:
        return False, str(_error(status, data))
    seen = 0
    for d in data.get("data", []):
        st = (d.get("status") or "").lower()
        st = st if st in dict(T.STATUSES) else (T.DISABLED if st else T.PENDING)
        comps = {c.get("type"): c for c in d.get("components", [])}
        t, created = T.objects.get_or_create(name=d.get("name", "")[:80], language=d.get("language", "en")[:10], defaults={
            "category": d.get("category", "UTILITY")[:16], "purpose": T.OTHER, "body": (comps.get("BODY") or {}).get("text", "")[:1024],
            "header": (comps.get("HEADER") or {}).get("text", "")[:60], "footer": (comps.get("FOOTER") or {}).get("text", "")[:60]})
        t.meta_id, t.status, t.synced_at = str(d.get("id", "")), st, timezone.now()
        t.rejected_reason = (d.get("rejected_reason") or "")[:300] if st == T.REJECTED else ""
        t.save()
        seen += 1
    return True, f"{seen} template{'s' if seen != 1 else ''} checked with Meta."


def delete_template(t) -> tuple[bool, str]:
    waba = get_credential("whatsapp", "waba_id")
    if t.meta_id and waba:
        status, data = _graph("DELETE", f"{waba}/message_templates", params={"name": t.name, **({"hsm_id": t.meta_id} if t.meta_id else {})})
        if status >= 400:
            return False, str(_error(status, data))
    t.delete()
    return True, "Template deleted."


def default_templates() -> list[dict]:
    """Ready-to-submit templates for everything the system sends on its own. Wording follows Meta's utility-template rules."""
    from apps.core.models import CompanySetting

    co = CompanySetting.load().name or "Pocket of Paradise"
    return [
        {"name": "staff_alert", "purpose": "notification", "variables": "name,title,body,link",
         "body": f"Hello {{{{1}}}}, you have a new update in the {co} system: {{{{2}}}}. Details: {{{{3}}}}. Open it here: {{{{4}}}} to take action. Thank you."},
        {"name": "new_lead_assigned", "purpose": "lead_assigned", "variables": "name,title,body,link",
         "body": f"Hello {{{{1}}}}, you have a new lead to handle at {co}: {{{{2}}}}. Client: {{{{3}}}}. Open the lead here: {{{{4}}}} and contact them soon. Thank you."},
        {"name": "travel_reminder_agent", "purpose": "travel_agent", "variables": "name,client,date,trip,link",
         "body": f"Hello {{{{1}}}}, a reminder from {co}: your client {{{{2}}}} travels on {{{{3}}}} for {{{{4}}}}. The full travel-day preview is here: {{{{5}}}} so you can check everything is ready."},
        {"name": "travel_reminder_client", "purpose": "travel_client", "variables": "name,trip,date,needs,link",
         "body": f"Hello {{{{1}}}}, your {{{{2}}}} with {co} starts on {{{{3}}}}. Please have ready: {{{{4}}}}. Your itinerary, payments and contacts are here: {{{{5}}}} and we look forward to hosting you."},
    ]


def ensure_default_templates() -> int:
    from .models import WhatsAppTemplate as T

    n = 0
    for d in default_templates():
        _, created = T.objects.get_or_create(name=d["name"], language="en", defaults={"purpose": d["purpose"], "body": d["body"], "variables": d["variables"], "category": "UTILITY"})
        n += created
    return n


def go_live_checklist() -> list[dict]:
    """What is left before WhatsApp and AI are fully live, in order. Shown on the ICT dashboard and the WhatsApp page."""
    from apps.accounts.models import User

    from . import ai
    from .models import MessagingSettings, WhatsAppTemplate, WhatsAppWindow

    c, cfg = creds(), MessagingSettings.get()
    approved = set(WhatsAppTemplate.objects.filter(status=WhatsAppTemplate.APPROVED).values_list("purpose", flat=True))
    execs = [u for u in User.objects.filter(is_active=True).prefetch_related("groups").select_related("employee") if u.role_names.intersection(cfg.exec_role_names)]
    return [
        {"label": "WhatsApp access token and phone number ID", "ok": configured(), "url": "ict:whatsapp"},
        {"label": "WhatsApp Business Account ID (for templates)", "ok": bool(c["waba_id"]), "url": "ict:whatsapp"},
        {"label": "Webhook verified in Meta (a message has come in)", "ok": WhatsAppWindow.objects.exists(), "url": "ict:whatsapp"},
        {"label": "App secret saved (checks messages really come from Meta)", "ok": bool(c["app_secret"] or get_credential("meta", "app_secret")), "url": "ict:whatsapp"},
        {"label": "Templates approved: staff alerts, new leads, travel reminders", "ok": {"notification", "lead_assigned", "travel_agent", "travel_client"} <= approved, "url": "ict:whatsapp"},
        {"label": "System address for links in messages", "ok": bool(cfg.public_base_url), "url": "ict:whatsapp"},
        {"label": "AI key (Claude or OpenAI)", "ok": ai.configured(), "url": "ict:ai"},
        {"label": "Director / COO WhatsApp numbers on their profiles", "ok": bool(execs) and all(u.whatsapp_target for u in execs), "url": "ict:ai"},
    ]
