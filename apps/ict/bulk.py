"""Bulk SMS (Africa's Talking) and bulk e-mail (Brevo): audiences, merge fields, opt-outs, sending in batches.

Live when the keys are saved under Administration → Integration Keys; otherwise a clearly-labelled mock that only records."""
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.core.phone import normalise_phone
from apps.system.services import get_credential

from .models import Broadcast, BroadcastRecipient, OptOut

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SMS_BATCH = 100
EMAIL_PER_RUN = 150
SIGN_SALT = "bulk-unsubscribe"


# ------------------------------------------------------------------ modes and credentials
def sms_live() -> bool:
    return bool(get_credential("sms", "api_key") and get_credential("sms", "username"))


def email_live() -> bool:
    return bool(get_credential("brevo", "api_key") and get_credential("brevo", "sender_email"))


def is_live(channel: str) -> bool:
    return sms_live() if channel == Broadcast.SMS else email_live()


# ------------------------------------------------------------------ audience
def audience_options():
    from apps.frontdesk.models import BusinessCard
    from apps.leads.models import Source, Stage, source_choices

    return {"sources": source_choices(), "stages": Stage.choices, "card_categories": BusinessCard.CATEGORIES}


def _first(name: str) -> str:
    return (name or "").strip().split(" ")[0] if (name or "").strip() else ""


def build_audience(spec: dict, channel: str):
    """-> (recipients, skipped) from an audience spec. Recipients are unique on the channel's address; skipped = [(name, reason)]."""
    from apps.contacts.models import Client
    from apps.frontdesk.models import BusinessCard

    raw = []
    c = spec.get("clients") or {}
    if c.get("enabled"):
        qs = Client.objects.alive()
        if c.get("sources"):
            qs = qs.filter(enquiries__source__in=c["sources"])
        if c.get("stages"):
            qs = qs.filter(enquiries__stage__in=c["stages"])
        if c.get("market") == "domestic":
            qs = qs.filter(is_international=False)
        elif c.get("market") == "international":
            qs = qs.filter(is_international=True)
        for cl in qs.distinct():
            raw.append({"name": cl.full_name, "email": cl.email, "phone": cl.phone, "origin": "client", "opt_out": cl.marketing_opt_out})
    k = spec.get("cards") or {}
    if k.get("enabled"):
        qs = BusinessCard.objects.all()
        if k.get("categories"):
            qs = qs.filter(category__in=k["categories"])
        for cd in qs:
            raw.append({"name": cd.name, "email": cd.email, "phone": cd.phone, "origin": "card", "opt_out": cd.marketing_opt_out})
    for line in (spec.get("manual") or "").replace(";", "\n").replace(",", "\n").splitlines():
        line = line.strip()
        if line:
            raw.append({"name": "", "email": line if "@" in line else "", "phone": "" if "@" in line else line, "origin": "manual", "opt_out": False})

    opted = {(o.kind, o.value) for o in OptOut.objects.all()}
    seen, out, skipped = set(), [], []
    for r in raw:
        if channel == Broadcast.SMS:
            addr = normalise_phone(r["phone"])
            valid = bool(addr) and re.fullmatch(r"\+\d{9,15}", addr) is not None
            key, kind = addr, "phone"
        else:
            addr = (r["email"] or "").strip().lower()
            valid = bool(EMAIL_RE.match(addr))
            key, kind = addr, "email"
        label = r["name"] or r["phone"] or r["email"] or "?"
        if not valid:
            skipped.append((label, "no valid " + ("phone number" if channel == Broadcast.SMS else "e-mail address")))
        elif r["opt_out"] or (kind, key) in opted:
            skipped.append((label, "opted out"))
        elif key in seen:
            continue
        else:
            seen.add(key)
            out.append({"name": r["name"], "email": addr if kind == "email" else r["email"], "phone": addr if kind == "phone" else r["phone"], "origin": r["origin"]})
    return out, skipped


# ------------------------------------------------------------------ message text
def merge(text: str, rec: dict) -> str:
    name = rec.get("name", "")
    return (text.replace("{first_name}", _first(name) or "there").replace("{name}", name or "there").replace("{company}", "Pocket of Paradise Travel Company"))


def sms_segments(text: str) -> int:
    n = len(text)
    return 1 if n <= 160 else math.ceil(n / 153)


def unsubscribe_token(kind: str, value: str) -> str:
    return signing.dumps({"k": kind, "v": value}, salt=SIGN_SALT)


def read_unsubscribe(token: str):
    try:
        d = signing.loads(token, salt=SIGN_SALT, max_age=60 * 60 * 24 * 365)
        return d["k"], d["v"]
    except (signing.BadSignature, KeyError):
        return None


def email_html(body: str, rec: dict, base_url: str) -> tuple[str, str]:
    from django.utils.html import escape, linebreaks

    from apps.core.models import CompanySetting

    co = CompanySetting.load()
    text = merge(body, rec)
    link = f"{base_url}/ict/unsubscribe/{unsubscribe_token('email', (rec.get('email') or '').lower())}/"
    html = (f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#2B2014"><div style="background:#1F6F6B;color:#fff;padding:16px 20px;border-radius:10px 10px 0 0;font-size:18px;font-weight:700">{escape(co.name)}</div>'
            f'<div style="padding:20px;border:1px solid #EDE3D3;border-top:0;font-size:15px;line-height:1.55">{linebreaks(escape(text))}</div>'
            f'<p style="font-size:12px;color:#6E6250;text-align:center">{escape(co.name)}{(" · " + escape(co.address)) if co.address else ""}<br>You are receiving this because you contacted us. <a href="{link}">Unsubscribe</a></p></div>')
    return html, text + f"\n\n--\nUnsubscribe: {link}"


# ------------------------------------------------------------------ providers
class ProviderError(Exception):
    pass


def _post(url, headers, data, timeout=25):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, TimeoutError) as e:
        raise ProviderError(f"Could not reach the provider: {e}") from e


def send_sms_batch(numbers: list[str], text: str) -> dict:
    """Africa's Talking: returns {number: (ok, detail)}. One message body per call (used for the batch of identical text)."""
    user, key, sender = get_credential("sms", "username"), get_credential("sms", "api_key"), get_credential("sms", "sender_id")
    host = "api.sandbox.africastalking.com" if user == "sandbox" else "api.africastalking.com"
    form = {"username": user, "to": ",".join(numbers), "message": text}
    if sender:
        form["from"] = sender
    status, body = _post(f"https://{host}/version1/messaging", {"apiKey": key, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                         urllib.parse.urlencode(form).encode())
    if status not in (200, 201):
        raise ProviderError(f"Africa's Talking error {status}: {body[:160]}")
    out = {}
    try:
        for r in json.loads(body)["SMSMessageData"]["Recipients"]:
            ok = r.get("status") == "Success" or str(r.get("statusCode")) in ("100", "101", "102")
            out[normalise_phone(r.get("number", ""))] = (ok, r.get("messageId") if ok else r.get("status", "failed"))
    except (ValueError, KeyError):
        raise ProviderError(f"Unexpected reply from Africa's Talking: {body[:160]}")
    return out


def send_email_one(to_email: str, to_name: str, subject: str, html: str, text: str) -> str:
    key = get_credential("brevo", "api_key")
    payload = {"sender": {"name": get_credential("brevo", "sender_name") or "Pocket of Paradise Travel Company", "email": get_credential("brevo", "sender_email")},
               "to": [{"email": to_email, **({"name": to_name} if to_name else {})}], "subject": subject, "htmlContent": html, "textContent": text}
    status, body = _post("https://api.brevo.com/v3/smtp/email", {"api-key": key, "accept": "application/json", "content-type": "application/json"}, json.dumps(payload).encode())
    if status not in (200, 201):
        try:
            msg = json.loads(body).get("message", body)
        except ValueError:
            msg = body
        raise ProviderError(f"Brevo {status}: {msg[:160]}")
    try:
        return json.loads(body).get("messageId", "")
    except ValueError:
        return ""


# ------------------------------------------------------------------ lifecycle
@transaction.atomic
def prepare(b: Broadcast) -> tuple[int, int]:
    """Freeze the audience into recipient rows (so what was sent is on record). Returns (recipients, skipped)."""
    b.recipients.all().delete()
    recs, skipped = build_audience(b.audience, b.channel)
    BroadcastRecipient.objects.bulk_create([BroadcastRecipient(broadcast=b, name=r["name"][:160], email=r["email"][:160], phone=r["phone"][:30], origin=r["origin"]) for r in recs])
    BroadcastRecipient.objects.bulk_create([BroadcastRecipient(broadcast=b, name=n[:160], status=BroadcastRecipient.SKIPPED, error=why) for n, why in skipped])
    return len(recs), len(skipped)


def start(b: Broadcast, when=None):
    """Queue it now (status sending) or for a later time (scheduled). The job sends in batches."""
    n, _skipped = prepare(b)
    if n == 0:
        raise ValueError("No one to send to: the audience is empty or everyone is opted out / has no valid address.")
    if b.channel == Broadcast.EMAIL and not b.subject.strip():
        raise ValueError("Give the e-mail a subject.")
    if not b.body.strip():
        raise ValueError("Write the message first.")
    if when and when > timezone.now():
        b.status, b.scheduled_at = Broadcast.SCHEDULED, when
    else:
        b.status, b.scheduled_at, b.started_at = Broadcast.SENDING, None, timezone.now()
    b.live = is_live(b.channel)
    b.save()
    return n


def cancel(b: Broadcast):
    if b.status in (Broadcast.SCHEDULED, Broadcast.SENDING, Broadcast.DRAFT):
        b.status = Broadcast.CANCELLED
        b.save(update_fields=["status"])


def process(b: Broadcast, base_url: str = "", limit: int | None = None) -> dict:
    """Send the next batch of pending recipients. Safe to call repeatedly; finishes the broadcast when none are left."""
    b.refresh_from_db()
    if b.status == Broadcast.SCHEDULED and b.scheduled_at and b.scheduled_at <= timezone.now():
        b.status, b.started_at, b.live = Broadcast.SENDING, timezone.now(), is_live(b.channel)
        b.save(update_fields=["status", "started_at", "live"])
    if b.status != Broadcast.SENDING:
        return b.counts()
    live = is_live(b.channel)
    pending = list(b.recipients.filter(status=BroadcastRecipient.PENDING)[: (limit or (SMS_BATCH if b.channel == Broadcast.SMS else EMAIL_PER_RUN))])
    now = timezone.now()
    if b.channel == Broadcast.SMS:
        if pending:
            msgs = {}
            for r in pending:
                msgs.setdefault(merge(b.body, {"name": r.name}), []).append(r)
            for text, rs in msgs.items():
                try:
                    res = send_sms_batch([r.phone for r in rs], text) if live else {r.phone: (True, "mock") for r in rs}
                except ProviderError as e:
                    for r in rs:
                        r.status, r.error = BroadcastRecipient.FAILED, str(e)[:240]
                        r.save(update_fields=["status", "error"])
                    continue
                for r in rs:
                    ok, detail = res.get(r.phone, (False, "no reply for this number"))
                    r.status, r.sent_at = (BroadcastRecipient.SENT if ok else BroadcastRecipient.FAILED), now
                    r.provider_id, r.error = (detail if ok else ""), ("" if ok else str(detail)[:240])
                    r.save(update_fields=["status", "sent_at", "provider_id", "error"])
    else:
        for r in pending:
            try:
                html, text = email_html(b.body, {"name": r.name, "email": r.email}, base_url or "")
                subj = merge(b.subject, {"name": r.name})
                r.provider_id = send_email_one(r.email, r.name, subj, html, text) if live else "mock"
                r.status, r.sent_at, r.error = BroadcastRecipient.SENT, now, ""
            except ProviderError as e:
                r.status, r.error = BroadcastRecipient.FAILED, str(e)[:240]
            r.save(update_fields=["status", "sent_at", "provider_id", "error"])
    if not b.recipients.filter(status=BroadcastRecipient.PENDING).exists():
        b.status, b.finished_at = Broadcast.SENT, timezone.now()
        b.save(update_fields=["status", "finished_at"])
    return b.counts()


def retry_failed(b: Broadcast):
    b.recipients.filter(status=BroadcastRecipient.FAILED).update(status=BroadcastRecipient.PENDING, error="")
    b.status, b.finished_at = Broadcast.SENDING, None
    b.save(update_fields=["status", "finished_at"])


def send_test(channel: str, to: str, subject: str, body: str, base_url: str = "") -> str:
    """One message to the person writing it, so they can see it before it goes to everyone."""
    rec = {"name": "Test", "email": to}
    if channel == Broadcast.SMS:
        number = normalise_phone(to)
        if not re.fullmatch(r"\+\d{9,15}", number or ""):
            raise ValueError("Enter a valid phone number for the test.")
        if sms_live():
            ok, detail = send_sms_batch([number], merge(body, rec))[number]
            if not ok:
                raise ValueError(f"The provider refused it: {detail}")
            return f"Test SMS sent to {number}."
        return f"Mock mode: nothing was sent. The test to {number} would read: “{merge(body, rec)}”"
    if not EMAIL_RE.match(to or ""):
        raise ValueError("Enter a valid e-mail address for the test.")
    html, text = email_html(body, rec, base_url)
    if email_live():
        send_email_one(to, "Test", "[Test] " + merge(subject, rec), html, text)
        return f"Test e-mail sent to {to}."
    return f"Mock mode: nothing was sent. A test e-mail to {to} would use subject “{merge(subject, rec)}”."


def due_broadcasts():
    return Broadcast.objects.filter(status__in=[Broadcast.SENDING, Broadcast.SCHEDULED]).filter(
        models_q_due())


def models_q_due():
    from django.db.models import Q

    return Q(status=Broadcast.SENDING) | Q(status=Broadcast.SCHEDULED, scheduled_at__lte=timezone.now())
