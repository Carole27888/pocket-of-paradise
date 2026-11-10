from django.conf import settings
from django.db import models
from django.utils import timezone


class BotSettings(models.Model):
    """Singleton. The safety guardrails live in code (services.GUARDRAILS) and can't be edited here; this only adds tone and facts."""

    enabled = models.BooleanField(default=False, help_text="When off, WhatsApp messages are saved and staff are alerted, but nobody is auto-replied.")
    bot_name = models.CharField(max_length=40, default="Zuri")
    greeting = models.CharField(max_length=300, default="Jambo! Karibu Pocket of Paradise Travel Company. How can I help plan your trip?")
    extra_instructions = models.TextField(blank=True, help_text="Extra tone or facts for the bot, e.g. current promotions. It cannot override the built-in safety rules.")
    max_bot_replies = models.PositiveSmallIntegerField(default=12, help_text="After this many bot replies in one chat, hand over to a person.")
    handoff_alert_roles = models.CharField(max_length=120, default="Sales Manager,Admin", help_text="Comma-separated roles alerted on hand-over.")
    fallback_message = models.TextField(blank=True, default="Thank you for your message. One of our travel consultants will reply to you shortly.", help_text="Sent automatically when the AI cannot reply (no credit, key invalid, network down). Leave blank to send nothing.")
    ai_fallback = models.BooleanField(default=True, help_text="If the main AI fails, try the other one (needs both keys saved).")

    class Meta:
        verbose_name_plural = "bot settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BotConversation(models.Model):
    BOT, HUMAN, CLOSED = "bot", "human", "closed"
    STATUSES = [(BOT, "Bot is replying"), (HUMAN, "With a person"), (CLOSED, "Closed")]

    phone = models.CharField(max_length=24, db_index=True)
    display_name = models.CharField(max_length=120, blank=True)
    client = models.ForeignKey("contacts.Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=8, choices=STATUSES, default=BOT, db_index=True)
    handoff_reason = models.CharField(max_length=160, blank=True)
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    bot_replies = models.PositiveSmallIntegerField(default=0)
    lead_captured = models.BooleanField(default=False)
    started_at = models.DateTimeField(default=timezone.now)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    unread = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.display_name or self.phone}"


class BotMessage(models.Model):
    CLIENT, BOT, AGENT = "client", "bot", "agent"
    conversation = models.ForeignKey(BotConversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=6, choices=[(CLIENT, "Client"), (BOT, "Bot"), (AGENT, "Agent")])
    body = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    external_id = models.CharField(max_length=80, blank=True, db_index=True)

    class Meta:
        ordering = ["created_at", "pk"]



class OptOut(models.Model):
    """Someone who asked to stop bulk messages. Checked on every send, whatever list they are on."""

    EMAIL, PHONE = "email", "phone"
    kind = models.CharField(max_length=5, choices=[(EMAIL, "E-mail"), (PHONE, "Phone")])
    value = models.CharField(max_length=160, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("kind", "value")]


class Broadcast(models.Model):
    """One bulk SMS or e-mail: the message, who it goes to, when, and how it went."""

    SMS, EMAIL = "sms", "email"
    CHANNELS = [(SMS, "SMS"), (EMAIL, "E-mail")]
    DRAFT, SCHEDULED, SENDING, SENT, CANCELLED = "draft", "scheduled", "sending", "sent", "cancelled"
    STATUSES = [(DRAFT, "Draft"), (SCHEDULED, "Scheduled"), (SENDING, "Sending"), (SENT, "Sent"), (CANCELLED, "Cancelled")]

    channel = models.CharField(max_length=5, choices=CHANNELS, db_index=True)
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    audience = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    live = models.BooleanField(default=False, help_text="True when it was sent through the real provider; False when sent in mock mode")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def counts(self):
        from django.db.models import Count

        c = dict(self.recipients.values_list("status").annotate(n=Count("pk")))
        total = sum(c.values())
        return {"total": total, "pending": c.get("pending", 0), "sent": c.get("sent", 0), "failed": c.get("failed", 0), "skipped": c.get("skipped", 0),
                "done_pct": round(100 * (total - c.get("pending", 0)) / total) if total else 0}


class BroadcastRecipient(models.Model):
    PENDING, SENT, FAILED, SKIPPED = "pending", "sent", "failed", "skipped"
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="recipients")
    name = models.CharField(max_length=160, blank=True)
    email = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    origin = models.CharField(max_length=14, blank=True, help_text="client / card / manual")
    status = models.CharField(max_length=8, default=PENDING, db_index=True)
    error = models.CharField(max_length=240, blank=True)
    provider_id = models.CharField(max_length=80, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]



class WebhookDelivery(models.Model):
    """One call to a public webhook (website form or Meta): what arrived, and whether it became a lead."""

    WEBSITE, META, WHATSAPP = "website", "meta", "whatsapp"
    source = models.CharField(max_length=10, db_index=True)
    ok = models.BooleanField(default=True)
    status_code = models.PositiveSmallIntegerField(default=200)
    ip = models.GenericIPAddressField(null=True, blank=True)
    summary = models.CharField(max_length=240, blank=True)
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class MetaEvent(models.Model):
    """Every item Meta sends, stored once (Meta re-sends deliveries) and then processed; failed ones are retried."""

    LEADGEN, MESSAGE, WHATSAPP = "leadgen", "message", "whatsapp"
    RECEIVED, PROCESSED, FAILED, IGNORED = "received", "processed", "failed", "ignored"
    kind = models.CharField(max_length=10, db_index=True)
    external_id = models.CharField(max_length=120, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, default=RECEIVED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=240, blank=True)
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_test = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class JsonBackupSettings(models.Model):
    """Singleton: how often a portable backup is prepared for ICT to download."""

    class Frequency(models.TextChoices):
        DAILY = "daily", "Every day"
        WEEKLY = "weekly", "Every week"
        MONTHLY = "monthly", "Every month"

    frequency = models.CharField(max_length=8, choices=Frequency.choices, default=Frequency.WEEKLY)
    keep = models.PositiveSmallIntegerField(default=8, help_text="How many backup files to keep on the server.")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class JsonBackupRun(models.Model):
    """One portable, plain-JSON copy of the data. Meant to leave the building in someone's hands; the first download closes it."""

    file = models.FileField(upload_to="backups/")
    period_label = models.CharField(max_length=40)
    object_count = models.PositiveIntegerField(default=0)
    size = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)
    downloaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_pending(self):
        return self.downloaded_at is None


class WorkLogEntry(models.Model):
    """What ICT did outside what the rest of the module already tracks: a repair, an install, a one-off fix."""

    employee = models.ForeignKey("people.Employee", on_delete=models.CASCADE, related_name="ict_work_log")
    date = models.DateField(default=timezone.localdate, db_index=True)
    description = models.CharField(max_length=300)
    started_at = models.TimeField(null=True, blank=True)
    ended_at = models.TimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-created_at"]
        verbose_name_plural = "work log entries"

    def __str__(self):
        return f"{self.date} · {self.description[:40]}"


class KnowledgeEntry(models.Model):
    """One thing the bot has been taught: a question customers ask and the answer the team approved. Answers are used word for word."""

    MANUAL, CHAT, CATALOG = "manual", "chat", "catalog"
    question = models.CharField(max_length=240)
    answer = models.TextField(max_length=900)
    keywords = models.CharField(max_length=240, blank=True, help_text="Other words customers use for this, comma separated")
    category = models.CharField(max_length=40, blank=True, help_text="e.g. Payments, Visas, Packing, Mara")
    source = models.CharField(max_length=8, choices=[(MANUAL, "Written by the team"), (CHAT, "From a chat"), (CATALOG, "From the catalogue")], default=MANUAL)
    is_active = models.BooleanField(default=True)
    times_used = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["category", "question"]
        verbose_name_plural = "knowledge entries"

    def __str__(self):
        return self.question


class MessagingSettings(models.Model):
    """Singleton, edited in ICT → WhatsApp and ICT → AI: who gets what on WhatsApp, the travel reminders, the director's assistant and AI prices."""

    notifications_enabled = models.BooleanField(default=True, help_text="Send staff alerts on WhatsApp / e-mail as each person chose. Off: alerts stay in the app only.")
    public_base_url = models.URLField(blank=True, help_text="The address people open the system on, e.g. https://erp.pocketofparadise.co.ke. Links in WhatsApp messages start with it. Filled in automatically the first time this page is saved.")
    travel_reminder_days = models.CharField(max_length=40, default="5", help_text="Days before departure to remind the agent and the client. Several are allowed, e.g. 5,1")
    remind_agents = models.BooleanField(default=True)
    remind_clients = models.BooleanField(default=True)
    reminder_hour = models.PositiveSmallIntegerField(default=8, help_text="Hour of the day (0-23) reminders go out")
    max_retries = models.PositiveSmallIntegerField(default=4, help_text="How many times a failed WhatsApp message is tried again")
    exec_assistant_enabled = models.BooleanField("Business assistant on WhatsApp", default=True,
                                                 help_text="The director and COO can text the WhatsApp number and ask about leads, sales, agents and revenue.")
    exec_roles = models.CharField(max_length=120, default="Director,COO", help_text="Comma-separated roles whose WhatsApp numbers may ask about the business")
    claude_input_price = models.DecimalField("Claude input, USD per million tokens", max_digits=8, decimal_places=3, default=3)
    claude_output_price = models.DecimalField("Claude output, USD per million tokens", max_digits=8, decimal_places=3, default=15)
    openai_input_price = models.DecimalField("OpenAI input, USD per million tokens", max_digits=8, decimal_places=3, default=0.15)
    openai_output_price = models.DecimalField("OpenAI output, USD per million tokens", max_digits=8, decimal_places=3, default=0.6)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "messaging settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def reminder_days(self) -> list[int]:
        out = []
        for p in self.travel_reminder_days.replace(" ", "").split(","):
            if p.isdigit() and 0 <= int(p) <= 60 and int(p) not in out:
                out.append(int(p))
        return sorted(out, reverse=True) or [5]

    @property
    def exec_role_names(self) -> list[str]:
        return [r.strip() for r in self.exec_roles.split(",") if r.strip()]


class WhatsAppTemplate(models.Model):
    """A message template approved by Meta. WhatsApp only allows business-started messages outside the 24-hour window when they use one."""

    NOTIFICATION, LEAD, TRAVEL_AGENT, TRAVEL_CLIENT, OTHER = "notification", "lead_assigned", "travel_agent", "travel_client", "other"
    PURPOSES = [(NOTIFICATION, "Any staff alert"), (LEAD, "New lead for an agent"), (TRAVEL_AGENT, "Travel reminder to the agent"),
                (TRAVEL_CLIENT, "Travel reminder to the client"), (OTHER, "Other / marketing")]
    DRAFT, PENDING, APPROVED, REJECTED, PAUSED, DISABLED = "draft", "pending", "approved", "rejected", "paused", "disabled"
    STATUSES = [(DRAFT, "Draft (not sent to Meta)"), (PENDING, "Waiting for Meta"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (PAUSED, "Paused by Meta"), (DISABLED, "Disabled")]
    CATEGORIES = [("UTILITY", "Utility (alerts, reminders)"), ("MARKETING", "Marketing"), ("AUTHENTICATION", "Authentication")]

    name = models.SlugField(max_length=80, help_text="Lower case and underscores only, e.g. staff_alert")
    language = models.CharField(max_length=10, default="en", help_text="e.g. en, en_US, sw")
    category = models.CharField(max_length=16, choices=CATEGORIES, default="UTILITY")
    purpose = models.CharField(max_length=16, choices=PURPOSES, default=NOTIFICATION, db_index=True)
    header = models.CharField(max_length=60, blank=True, help_text="Optional bold first line (no variables)")
    body = models.TextField(max_length=1024, help_text="Use {{1}}, {{2}}… for the parts that change. A variable may not be the very first or last thing.")
    footer = models.CharField(max_length=60, blank=True)
    variables = models.CharField(max_length=300, blank=True, help_text="What each variable is filled with, in order, comma separated (see the list on the page)")
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    meta_id = models.CharField(max_length=60, blank=True)
    rejected_reason = models.CharField(max_length=300, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["purpose", "name"]
        unique_together = [("name", "language")]

    def __str__(self):
        return f"{self.name} ({self.language})"

    @property
    def variable_names(self) -> list[str]:
        return [v.strip() for v in self.variables.split(",") if v.strip()]

    @property
    def variable_count(self) -> int:
        import re

        return len(set(re.findall(r"\{\{(\d+)\}\}", self.body)))

    def render(self, params: list) -> str:
        text = self.body
        for i, p in enumerate(params, 1):
            text = text.replace("{{%d}}" % i, str(p))
        return "\n".join(x for x in (self.header, text, self.footer) if x)


class WhatsAppWindow(models.Model):
    """When each number last wrote to us. Inside 24 hours of that, a free-text reply is allowed; after it, only a template."""

    phone = models.CharField(max_length=24, unique=True)
    last_inbound_at = models.DateTimeField(default=timezone.now)

    @property
    def is_open(self) -> bool:
        from datetime import timedelta

        return self.last_inbound_at >= timezone.now() - timedelta(hours=23, minutes=50)


class AICall(models.Model):
    """One request to an AI provider, with the tokens it used and an estimated cost."""

    feature = models.CharField(max_length=30, db_index=True, help_text="bot, assistant, exec, coach, writer…")
    provider = models.CharField(max_length=10, db_index=True)
    model = models.CharField(max_length=60, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=5, default=0)
    ok = models.BooleanField(default=True)
    error = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class ExecChat(models.Model):
    """A question the director or COO asked the business assistant on WhatsApp, and the answer they got."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    phone = models.CharField(max_length=24, db_index=True)
    question = models.TextField()
    answer = models.TextField(blank=True)
    provider = models.CharField(max_length=10, blank=True)
    ok = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
