"""Meta webhooks: Facebook and Instagram lead-form leads, Click-to-WhatsApp ad chats, WhatsApp messages, and Messenger / Instagram DMs.

One endpoint, /webhooks/meta/, subscribed in the Meta app to:
    page · leadgen                              → a lead form was submitted (details are fetched from the Graph API with the page token)
    page / instagram · messages                 → Messenger or Instagram DM → Marketing Unified Inbox
    whatsapp_business_account · messages        → WhatsApp message; one with an ad `referral` is a Click-to-WhatsApp lead

Every item is stored once as a MetaEvent (Meta re-sends deliveries), then processed. A lead whose details could not be fetched stays
"failed" and is retried every 15 minutes, up to MAX_ATTEMPTS times."""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.phone import normalise_phone
from apps.system.services import get_credential

from .models import MetaEvent, WebhookDelivery

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
GRAPH = "https://graph.facebook.com/v20.0"
OPEN_LEAD_DAYS = 30


class MetaError(Exception):
    pass


# ------------------------------------------------------------------ security
def verify_challenge(params):
    """The GET handshake Meta performs when the webhook is added: echo hub.challenge when the verify token matches."""
    token = get_credential("meta", "verify_token")
    if token and params.get("hub.mode") == "subscribe" and hmac.compare_digest(params.get("hub.verify_token", ""), token):
        return params.get("hub.challenge", "")
    return None


def signature_ok(body: bytes, header: str) -> bool:
    """X-Hub-Signature-256 = "sha256=" + HMAC-SHA256(app secret, raw body). Without an app secret nothing is accepted."""
    secret = get_credential("meta", "app_secret")
    if not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


def status() -> dict:
    return {"verify_token": bool(get_credential("meta", "verify_token")), "app_secret": bool(get_credential("meta", "app_secret")), "page_token": bool(get_credential("meta", "access_token"))}


# ------------------------------------------------------------------ intake
def receive(payload: dict, is_test: bool = False) -> list:
    """Store every item in a delivery, then process it. Returns the events (already-seen ones are skipped)."""
    events = []
    obj = payload.get("object", "")
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            field, value = ch.get("field", ""), ch.get("value", {}) or {}
            if field == "leadgen":
                ev = _store(MetaEvent.LEADGEN, f"lead:{value.get('leadgen_id')}", value, is_test)
                if ev:
                    events.append(ev)
            elif field == "messages" and obj == "whatsapp_business_account":
                if value.get("statuses"):                                   # sent / delivered / read / failed receipts for our messages
                    from . import whatsapp

                    try:
                        whatsapp.apply_statuses(value["statuses"])
                    except Exception:
                        log.exception("whatsapp statuses failed")
                for m in value.get("messages", []):
                    ev = _store(MetaEvent.WHATSAPP, f"wa:{m.get('id')}", {"message": m, "contacts": value.get("contacts", []), "metadata": value.get("metadata", {})}, is_test)
                    if ev:
                        events.append(ev)
        for m in entry.get("messaging", []):                                # Messenger and Instagram DMs
            mid = (m.get("message") or {}).get("mid")
            if mid and not (m.get("message") or {}).get("is_echo"):
                ev = _store(MetaEvent.MESSAGE, f"dm:{mid}", {"object": obj, "messaging": m}, is_test)
                if ev:
                    events.append(ev)
    for ev in events:
        process(ev)
    return events


def _store(kind, external_id, data, is_test):
    try:
        with transaction.atomic():                       # a savepoint, so a duplicate never poisons the caller's transaction
            return MetaEvent.objects.create(kind=kind, external_id=external_id[:120], payload=data, is_test=is_test)
    except IntegrityError:
        return None


def process(ev: MetaEvent) -> MetaEvent:
    ev.attempts += 1
    try:
        handler = {MetaEvent.LEADGEN: _lead_form, MetaEvent.WHATSAPP: _whatsapp, MetaEvent.MESSAGE: _dm}[ev.kind]
        result = handler(ev)
        ev.status, ev.error, ev.processed_at = (MetaEvent.IGNORED if result == "ignored" else MetaEvent.PROCESSED), "", timezone.now()
    except Exception as e:  # keep the delivery; retry later
        log.warning("meta event %s failed: %s", ev.pk, e)
        ev.status, ev.error = MetaEvent.FAILED, str(e)[:240]
    ev.save()
    WebhookDelivery.objects.create(source=WebhookDelivery.WHATSAPP if ev.kind == MetaEvent.WHATSAPP else WebhookDelivery.META, ok=ev.status != MetaEvent.FAILED, status_code=200,
                                   summary=f"{ev.get_kind_display() if hasattr(ev, 'get_kind_display') else ev.kind}: {ev.error or ev.status}"[:240], enquiry=ev.enquiry, is_test=ev.is_test)
    return ev


def retry_failed() -> int:
    n = 0
    for ev in MetaEvent.objects.filter(status=MetaEvent.FAILED, attempts__lt=MAX_ATTEMPTS, created_at__gte=timezone.now() - timedelta(days=2)):
        process(ev)
        n += ev.status == MetaEvent.PROCESSED
    return n


# ------------------------------------------------------------------ lead forms
def fetch_lead(leadgen_id: str) -> dict:
    token = get_credential("meta", "access_token")
    if not token:
        raise MetaError("No page access token saved (Integration Keys → Meta).")
    url = f"{GRAPH}/{urllib.parse.quote(str(leadgen_id))}?access_token={urllib.parse.quote(token)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except ValueError:
            msg = ""
        raise MetaError(f"Meta said {e.code}: {msg}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise MetaError(f"Could not reach Meta: {e}")


def parse_fields(field_data: list) -> dict:
    """Lead-form answers → our fields. Standard fields by name; anything else goes into the notes."""
    out, extra = {}, []
    for f in field_data or []:
        name, vals = (f.get("name") or "").lower(), f.get("values") or []
        val = ", ".join(map(str, vals)).strip()
        if not val:
            continue
        if name in ("full_name", "name") or ("name" in name and "first" not in name and "last" not in name and not out.get("name")):
            out["name"] = val
        elif name == "first_name":
            out["first"] = val
        elif name == "last_name":
            out["last"] = val
        elif name in ("phone_number", "phone", "mobile_number"):
            out["phone"] = val
        elif name == "email":
            out["email"] = val
        elif any(k in name for k in ("date", "when", "month")):
            out["date"] = val
            extra.append(f"{name.replace('_', ' ')}: {val}")
        elif any(k in name for k in ("travellers", "travelers", "people", "guests", "adults", "group", "pax")):
            out["adults"] = val
            extra.append(f"{name.replace('_', ' ')}: {val}")
        else:
            extra.append(f"{name.replace('_', ' ')}: {val}")
    if not out.get("name"):
        out["name"] = " ".join(x for x in (out.get("first"), out.get("last")) if x)
    out["notes"] = "; ".join(extra)
    return out


def campaign_for(*ids, name=""):
    from apps.marketing.models import Campaign

    ids = [str(i) for i in ids if i]
    for c in Campaign.objects.exclude(meta_ids=""):
        mine = {x.strip() for x in c.meta_ids.split(",") if x.strip()}
        if mine.intersection(ids):
            return c
    return Campaign.objects.filter(name__iexact=name).first() if name else None


def _lead_form(ev: MetaEvent):
    from apps.marketing import services as ms

    value = ev.payload
    data = value.get("field_data") and value or fetch_lead(value.get("leadgen_id"))
    f = parse_fields(data.get("field_data"))
    if not f.get("name") or not (f.get("phone") or f.get("email")):
        raise MetaError("The lead form had no name and phone or e-mail.")
    platform = (data.get("platform") or value.get("platform") or "fb").lower()
    source = "instagram" if platform.startswith("ig") or "instagram" in platform else "facebook"
    camp = campaign_for(value.get("form_id"), value.get("ad_id"), value.get("adgroup_id"), value.get("campaign_id"), name=data.get("campaign_name", ""))
    notes = ("(Test lead) " if ev.is_test else "") + f"Lead form {value.get('form_id', '')}. " + f["notes"]
    try:
        adults = max(1, min(int("".join(c for c in f.get("adults", "2") if c.isdigit())[:2] or 2), 60))
    except ValueError:
        adults = 2
    enq = ms.ingest_lead(name=f["name"][:120], phone=f.get("phone", ""), email=f.get("email", ""), source=source, message=notes, adults=adults, campaign=camp, captured_via="meta_leadform")
    ev.enquiry = enq
    return "ok"


# ------------------------------------------------------------------ WhatsApp
def open_enquiry_for(phone: str):
    from apps.leads.models import Enquiry, Stage

    return (Enquiry.objects.filter(client__phone=phone, created_at__gte=timezone.now() - timedelta(days=OPEN_LEAD_DAYS)).exclude(stage__in=[Stage.COMPLETED, Stage.LOST])
            .order_by("-created_at").first())


def _whatsapp(ev: MetaEvent):
    from apps.leads import services as ls
    from apps.marketing import services as ms

    m = ev.payload.get("message", {})
    phone = normalise_phone(m.get("from", ""))
    name = next((c.get("profile", {}).get("name", "") for c in ev.payload.get("contacts", []) if c.get("wa_id") == m.get("from")), "")
    text = (m.get("text") or {}).get("body") or (m.get("button") or {}).get("text") or ((m.get("interactive") or {}).get("button_reply") or {}).get("title") or f"[{m.get('type', 'message')}]"
    ref = m.get("referral") or {}
    from . import exec_assistant, whatsapp

    whatsapp.mark_inbound(phone)                                       # they wrote: free-text replies are allowed for 24 hours
    if m.get("type") in ("text", "button", "interactive") and not ev.is_test and exec_assistant.handle(phone, text, m.get("id", "")):
        return "exec"                                                  # the director / COO asking about the business: never a lead, never the bot
    existing = open_enquiry_for(phone)
    if existing:                                                       # same person writing again: add to their open lead
        ls.log_activity(existing, "whatsapp", f"Customer wrote on WhatsApp: {text[:300]}", None)
        ev.enquiry = existing
    elif ref:                                                          # Click-to-WhatsApp ad: credit the ad
        url = (ref.get("source_url") or "").lower()
        source = "instagram" if "instagram" in url else "facebook"
        camp = campaign_for(ref.get("source_id"), ref.get("ctwa_clid"), name=ref.get("headline", ""))
        enq = ms.ingest_lead(name=name or phone, phone=phone, source=source, message=("(Test lead) " if ev.is_test else "") + f"Click-to-WhatsApp ad: {ref.get('headline', '')}. First message: {text[:300]}",
                             campaign=camp, captured_via="click_to_whatsapp")
        ev.enquiry = enq
    # the WhatsApp assistant answers (or hands over) in either case
    from . import services as bot

    try:
        bot.handle_inbound(phone, text, name=name, external_id=m.get("id", ""))
    except Exception:                                                  # the lead is already safe; never fail the webhook over the bot
        log.exception("bot could not handle inbound")
    return "ok"


# ------------------------------------------------------------------ Messenger / Instagram DMs
def _dm(ev: MetaEvent):
    from apps.marketing.models import Channel, InboxMessage

    m = ev.payload.get("messaging", {})
    key = "instagram" if ev.payload.get("object") == "instagram" else "facebook"
    channel = Channel.objects.filter(key=key).first()
    if channel is None:
        return "ignored"
    text = (m.get("message") or {}).get("text") or "[attachment]"
    InboxMessage.objects.get_or_create(channel=channel, external_id=(m.get("message") or {}).get("mid", "")[:60],
                                       defaults={"sender": f"{channel.name} user {str((m.get('sender') or {}).get('id', ''))[-4:]}", "message": text[:2000], "kind": "message",
                                                 "received_at": timezone.now()})
    return "ok"


# ------------------------------------------------------------------ tests from the settings page
def simulate(kind: str):
    """Push a sample through the real path. Creates a real enquiry marked "(Test lead)"."""
    import uuid

    uid = uuid.uuid4().hex[:10]
    if kind == "leadgen":
        payload = {"object": "page", "entry": [{"id": "0", "changes": [{"field": "leadgen", "value": {
            "leadgen_id": f"test-{uid}", "form_id": "test-form", "platform": "ig",
            "field_data": [{"name": "full_name", "values": ["Meta Test Lead"]}, {"name": "phone_number", "values": ["+254700123456"]}, {"name": "email", "values": ["meta.test@example.com"]},
                           {"name": "when_do_you_plan_to_travel", "values": ["August 2027"]}, {"name": "number_of_travellers", "values": ["2"]}]}}]}]}
    elif kind == "whatsapp":
        payload = {"object": "whatsapp_business_account", "entry": [{"id": "0", "changes": [{"field": "messages", "value": {
            "metadata": {"display_phone_number": "254700000000"}, "contacts": [{"wa_id": "254700123457", "profile": {"name": "Ad Test"}}],
            "messages": [{"from": "254700123457", "id": f"wamid.test{uid}", "type": "text", "text": {"body": "Hi, I saw your ad about the Mara safari"},
                          "referral": {"source_url": "https://fb.me/test", "source_id": "test-ad", "headline": "Maasai Mara 3 days"}}]}}]}]}
    else:
        return []
    return receive(payload, is_test=True)


def summary(days=7) -> dict:
    since = timezone.now() - timedelta(days=days)
    qs = MetaEvent.objects.filter(created_at__gte=since, is_test=False)
    return {"leads": qs.filter(kind="leadgen", status="processed").count(), "whatsapp": qs.filter(kind="whatsapp").count(), "dms": qs.filter(kind="message").count(),
            "failed": MetaEvent.objects.filter(status="failed").count()}
