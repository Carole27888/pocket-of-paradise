from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    LEVELS = [("info", "Info"), ("success", "Success"), ("warning", "Warning"), ("danger", "Alert")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=160)
    body = models.CharField(max_length=300, blank=True)
    url = models.CharField(max_length=300, blank=True)
    level = models.CharField(max_length=8, choices=LEVELS, default="info")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    dedupe_key = models.CharField(max_length=120, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class PushSubscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.URLField(max_length=600, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} · {self.user_agent[:40]}"


class OutboundMessage(models.Model):
    """Every SMS / WhatsApp / e-mail the system sends (or would send in mock mode): a visible audit trail."""

    CHANNELS = [("sms", "SMS"), ("whatsapp", "WhatsApp"), ("email", "E-mail")]
    STATUSES = [("queued", "Queued"), ("sent", "Sent"), ("delivered", "Delivered"), ("read", "Read"), ("failed", "Failed")]
    channel = models.CharField(max_length=10, choices=CHANNELS, db_index=True)
    to = models.CharField(max_length=160)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=STATUSES, default="queued", db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                             help_text="The staff member it was for, when it was an alert")
    provider_id = models.CharField(max_length=120, blank=True, db_index=True, help_text="The provider's message id (WhatsApp wamid), used to match delivery receipts")
    template = models.CharField(max_length=80, blank=True, help_text="WhatsApp template used, when sent outside the 24-hour window")
    payload = models.JSONField(default=dict, blank=True, help_text="What is needed to send it again: template name, language and parameters")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=20, default="mock")
    error = models.CharField(max_length=300, blank=True)
    purpose = models.CharField(max_length=40, blank=True, help_text="e.g. invoice_reminder, welcome, review_request")
    related = models.CharField(max_length=60, blank=True, help_text="e.g. invoice:42")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.to}"


class Announcement(models.Model):
    """A notice from management to the whole team, or to chosen roles, pinned to the top of everyone's Main menu until it expires."""

    NORMAL, IMPORTANT = "normal", "important"
    title = models.CharField(max_length=140)
    body = models.TextField(max_length=2000)
    audience = models.CharField(max_length=300, blank=True, help_text="Comma-separated roles. Empty means everyone.")
    level = models.CharField(max_length=10, choices=[(NORMAL, "Normal"), (IMPORTANT, "Important")], default=NORMAL)
    pinned = models.BooleanField(default=False)
    expires_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-pinned", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def roles(self):
        return [r.strip() for r in self.audience.split(",") if r.strip()]

    def visible_to(self, user) -> bool:
        if self.expires_on and self.expires_on < timezone.localdate():
            return False
        return not self.roles or user.is_superuser or bool(set(self.roles) & set(user.role_names))


def announcements_for(user, limit=None):
    today = timezone.localdate()
    qs = Announcement.objects.exclude(expires_on__lt=today)
    out = [a for a in qs if a.visible_to(user)]
    return out[:limit] if limit else out
