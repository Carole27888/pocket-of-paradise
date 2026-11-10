"""Every alert to a member of staff: in the app (the bell and a pop-up), web push, and WhatsApp and/or e-mail as the person chose on
My Profile. WhatsApp and e-mail go out after the database commit, through the job queue, and are logged with their delivery status."""
import json
import logging

from django.conf import settings
from django.utils import timezone

from .models import Notification, PushSubscription

log = logging.getLogger("apps.notifications")


def notify(user, title: str, body: str = "", url: str = "", level: str = "info", dedupe_key: str = "",
           push: bool = True, *, purpose: str = "", fields: dict | None = None, channels: bool = True) -> Notification | None:
    """`purpose` picks the WhatsApp template (lead_assigned, travel_agent…; default: the general staff alert); `fields` fill it.
    `channels=False` keeps it in the app only."""
    if user is None or not getattr(user, "is_active", True):
        return None
    if dedupe_key and Notification.objects.filter(user=user, dedupe_key=dedupe_key).exists():
        return None
    n = Notification.objects.create(user=user, title=title, body=body, url=url, level=level, dedupe_key=dedupe_key)
    if push:
        send_push(user, title, body, url)
    if channels and (user.wants_whatsapp or user.wants_email):
        from django.db import transaction

        from apps.core.jobs.runner import enqueue

        transaction.on_commit(lambda: enqueue("notifications.deliver", notification_id=n.pk, purpose=purpose, fields=fields or {}))
    return n


def absolute(url: str) -> str:
    """A link that works from a phone: the address set in ICT → WhatsApp, else the site address."""
    if not url or url.startswith(("http://", "https://")):
        return url
    base = ""
    try:
        from apps.ict.models import MessagingSettings

        base = MessagingSettings.get().public_base_url
    except Exception:
        pass
    return (base or settings.SITE_URL).rstrip("/") + "/" + url.lstrip("/")


def deliver(notification_id: int, purpose: str = "", fields: dict | None = None) -> str:
    """Send one in-app alert on the person's chosen channels. Runs as a background job."""
    from apps.ict.models import MessagingSettings
    from apps.integrations.messaging import send_message

    n = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if n is None:
        return "gone"
    if not MessagingSettings.get().notifications_enabled:
        return "channels off"
    u, link, done = n.user, absolute(n.url), []
    if u.wants_whatsapp and u.whatsapp_target:
        text = f"*{n.title}*" + (f"\n{n.body}" if n.body else "") + (f"\n{link}" if link else "")
        f = {"name": (u.full_name or "").split()[0] if u.full_name else "there", "title": n.title, "body": n.body or n.title, "link": link or absolute("/"), **(fields or {})}
        m = send_message("whatsapp", u.whatsapp_target, text, purpose=purpose or "notification", related=f"notification:{n.pk}", user=u, fields=f)
        done.append(f"whatsapp:{m.status}")
    if u.wants_email and u.email:
        m = send_message("email", u.email, f"{n.title}\n\n{n.body}\n\n{link}".strip(), subject=n.title, purpose=purpose or "notification", related=f"notification:{n.pk}", user=u)
        done.append(f"email:{m.status}")
    return ", ".join(done) or "no channel"


def send_push(user, title, body="", url="") -> int:
    if not (settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY):
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover
        return 0
    sent = 0
    payload = json.dumps({"title": title, "body": body, "url": url or "/"})
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
            )
            sub.last_used_at = timezone.now()
            sub.save(update_fields=["last_used_at"])
            sent += 1
        except WebPushException as exc:
            if getattr(exc, "response", None) is not None and exc.response.status_code in (404, 410):
                sub.delete()
            log.warning("push failed: %s", exc)
    return sent


def notify_roles(role_names, title, body="", url="", level="info", dedupe_key="", push=True, **kw) -> int:
    """Notify every active user holding any of `role_names` (a dedupe key is made unique per user)."""
    from apps.accounts.models import User

    n = 0
    for u in User.objects.filter(is_active=True, groups__name__in=list(role_names)).distinct():
        if notify(u, title, body, url, level, f"{dedupe_key}:{u.pk}" if dedupe_key else "", push, **kw):
            n += 1
    return n
