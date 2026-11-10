"""SMS / WhatsApp / e-mail behind the service layer. Mock mode records messages without calling any provider."""
import json
from datetime import timedelta
import logging
import os
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

from apps.core.phone import normalise_phone

from .base import BaseService, get_service, register
from .models import IntegrationLog

log = logging.getLogger("apps.integrations")


class MessagingService(BaseService):
    def send(self, to: str, body: str, subject: str = "") -> str:
        raise NotImplementedError


@register("sms", "mock")
class MockSMS(MessagingService):
    def send(self, to, body, subject=""):
        self.log("send", detail=f"to={to} len={len(body)}")
        return "mock-sms"


@register("sms", "live")
class LiveSMS(MessagingService):
    """Africa's Talking (keys from Integration Keys, or AT_USERNAME / AT_API_KEY). Username 'sandbox' uses their test environment."""

    def send(self, to, body, subject=""):
        from apps.system.services import get_credential

        user, key, sender = get_credential("sms", "username"), get_credential("sms", "api_key"), get_credential("sms", "sender_id")
        host = "api.sandbox.africastalking.com" if user == "sandbox" else "api.africastalking.com"
        form = {"username": user, "to": to, "message": body}
        if sender:
            form["from"] = sender
        req = urllib.request.Request(f"https://{host}/version1/messaging", data=urllib.parse.urlencode(form).encode(), headers={"apiKey": key, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            self.log("send", detail=r.read()[:500].decode("utf-8", "ignore"))
        return "at"


@register("whatsapp", "mock")
class MockWhatsApp(MessagingService):
    def send(self, to, body, subject=""):
        self.log("send", detail=f"to={to} len={len(body)}")
        return "mock-wa"


@register("whatsapp", "live")
class LiveWhatsApp(MessagingService):
    """WhatsApp Business Cloud API, keys from ICT → WhatsApp (see apps.ict.whatsapp, which also handles templates)."""

    def send(self, to, body, subject=""):
        from apps.ict import whatsapp

        res = whatsapp.send(to, body)
        self.log("send", detail=f"to={to} id={res['id']}")
        return res["id"]


@register("email", "mock")
class MockEmail(MessagingService):
    def send(self, to, body, subject=""):
        send_mail(subject or "Pocket of Paradise", body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)
        self.log("send", detail=f"to={to} subject={subject}")
        return "console"


@register("email", "live")
class LiveEmail(MockEmail):
    """Uses the EMAIL_URL SMTP configuration (same code path)."""


RETRY_MINUTES = [2, 10, 60, 240, 720]


def send_message(channel: str, to: str, body: str, *, subject: str = "", purpose: str = "", related: str = "", user=None, fields: dict | None = None):
    """Send and record. Returns the OutboundMessage (status sent/failed); never raises.

    WhatsApp goes through apps.ict.whatsapp, which picks free text or an approved template (Meta's 24-hour rule); `fields` fill the
    template's variables (name, title, body, link, date, trip…). A failed WhatsApp message is retried by ict.retry_whatsapp."""
    from apps.notifications.models import OutboundMessage

    target = normalise_phone(to) if channel in ("sms", "whatsapp") else (to or "").strip()
    msg = OutboundMessage.objects.create(channel=channel, to=target, subject=subject[:200], body=body, purpose=purpose[:40], related=related[:60], user=user,
                                         payload={"fields": fields or {}} if channel == "whatsapp" else {})
    if not target:
        msg.status, msg.error = "failed", "No recipient"
        msg.save(update_fields=["status", "error"])
        return msg
    return attempt(msg)


def attempt(msg):
    """One try at sending a logged message (the first send and every retry)."""
    from django.utils import timezone

    msg.attempts += 1
    update = ["status", "error", "provider", "attempts", "next_retry_at", "provider_id", "template"]
    try:
        if msg.channel == "whatsapp":
            from apps.ict import whatsapp

            if whatsapp.configured():
                res = whatsapp.send(msg.to, msg.body, purpose=msg.purpose, fields=(msg.payload or {}).get("fields"),
                                    force_template=bool((msg.payload or {}).get("force_template")))
                msg.provider, msg.provider_id, msg.template = "meta", res["id"][:120], res["template"][:80]
                IntegrationLog.objects.create(service="whatsapp", action="send", mode="live", detail=f"to={msg.to} id={res['id']} template={res['template'] or '-'}")
            else:
                msg.provider = "mock"
                get_service("whatsapp").send(msg.to, msg.body, msg.subject)
        else:
            svc = get_service(msg.channel)
            msg.provider = svc.mode
            svc.send(msg.to, msg.body, msg.subject)
        msg.status, msg.error, msg.next_retry_at = "sent", "", None
    except Exception as exc:  # provider failure must never break the workflow that triggered it
        log.warning("message %s failed: %s", msg.pk, exc)
        msg.status, msg.error = "failed", str(exc)[:300]
        retries = _max_retries()
        msg.next_retry_at = (timezone.now() + timedelta(minutes=RETRY_MINUTES[min(msg.attempts - 1, len(RETRY_MINUTES) - 1)])
                             if msg.channel == "whatsapp" and msg.attempts <= retries else None)
    msg.save(update_fields=update)
    return msg


def _max_retries() -> int:
    try:
        from apps.ict.models import MessagingSettings

        return MessagingSettings.get().max_retries
    except Exception:
        return 4


def retry_due(limit: int = 50) -> int:
    """Failed WhatsApp messages whose retry time has come are tried again."""
    from django.utils import timezone

    from apps.notifications.models import OutboundMessage

    n = 0
    for m in OutboundMessage.objects.filter(channel="whatsapp", status="failed", next_retry_at__lte=timezone.now())[:limit]:
        attempt(m)
        n += m.status == "sent"
    return n
