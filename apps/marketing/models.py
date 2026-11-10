from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core.fields import EncryptedCharField
from apps.core.models import TimeStampedModel


class Channel(models.Model):
    """A source of enquiries. `key` matches leads.Source codes so enquiries and spend line up."""

    key = models.SlugField(max_length=20, unique=True)
    name = models.CharField(max_length=40)
    is_social = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("marketing:channel", args=[self.key])


class SocialAccount(models.Model):
    channel = models.OneToOneField(Channel, on_delete=models.CASCADE, related_name="account")
    handle = models.CharField(max_length=80, blank=True)
    access_token = EncryptedCharField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    connected = models.BooleanField(default=False)
    followers = models.PositiveIntegerField(default=0)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.channel} {self.handle}"

    @property
    def status(self) -> str:
        if not self.connected:
            return "Not connected"
        if self.expires_at and self.expires_at < timezone.now() + timedelta(days=7):
            return "Expires soon" if self.expires_at > timezone.now() else "Expired"
        return "Connected"


class ChannelStatsDaily(models.Model):
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="stats")
    date = models.DateField(db_index=True)
    followers = models.PositiveIntegerField(default=0)
    reach = models.PositiveIntegerField(default=0)
    engagement_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    profile_views = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("channel", "date")]
        ordering = ["-date"]


class Campaign(TimeStampedModel):
    DRAFT, ACTIVE, PAUSED, DONE = "draft", "active", "paused", "completed"
    STATUSES = [(DRAFT, "Draft"), (ACTIVE, "Active"), (PAUSED, "Paused"), (DONE, "Completed")]
    OBJECTIVES = [("enquiries", "Generate enquiries"), ("bookings", "Drive bookings"), ("awareness", "Brand awareness"),
                  ("promo", "Seasonal promotion"), ("expo", "Expo / event leads")]

    name = models.CharField(max_length=140)
    objective = models.CharField(max_length=12, choices=OBJECTIVES, default="enquiries")
    channels = models.ManyToManyField(Channel, blank=True, related_name="campaigns")
    budget = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    audience_notes = models.CharField(max_length=240, blank=True)
    creative_notes = models.TextField(blank=True)
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="campaigns")
    meta_ids = models.CharField("Meta IDs", max_length=300, blank=True,
                                help_text="Meta campaign, ad set, ad or lead-form IDs, comma-separated. Leads from those ads are credited here. A campaign with the same name as the Meta campaign also matches.")

    class Meta:
        ordering = ["-start_date", "-pk"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("marketing:campaign", args=[self.pk])

    @property
    def spent(self) -> Decimal:
        return self.spend_rows.aggregate(t=models.Sum("spend"))["t"] or Decimal("0")

    @property
    def budget_used(self) -> int:
        return min(100, round(100 * self.spent / self.budget)) if self.budget else 0


class AdSpendDaily(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="spend_rows")
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="spend_rows")
    date = models.DateField(db_index=True)
    spend = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    impressions = models.PositiveIntegerField(default=0)
    reach = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    leads = models.PositiveIntegerField(default=0)
    posted_to_finance = models.BooleanField(default=False)

    class Meta:
        unique_together = [("campaign", "channel", "date")]
        ordering = ["-date"]


class MediaAsset(TimeStampedModel):
    KINDS = [("photo", "Photo"), ("video", "Video"), ("pdf", "Itinerary PDF"), ("other", "Other")]
    title = models.CharField(max_length=140)
    kind = models.CharField(max_length=6, choices=KINDS, default="photo")
    file = models.FileField(upload_to="marketing/%Y/%m/", blank=True)
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="media_assets")
    tags = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class HashtagSet(models.Model):
    name = models.CharField(max_length=60, unique=True)
    tags = models.TextField(help_text="Space or comma separated")

    def __str__(self):
        return self.name


class Post(TimeStampedModel):
    DRAFT, NEEDS, SCHEDULED, PUBLISHED = "draft", "needs_approval", "scheduled", "published"
    STATUSES = [(DRAFT, "Draft"), (NEEDS, "Needs approval"), (SCHEDULED, "Scheduled"), (PUBLISHED, "Published")]
    FORMATS = [("reel", "Reel"), ("video", "Video"), ("photo", "Photo"), ("carousel", "Carousel"),
               ("article", "Article"), ("live", "Live"), ("story", "Story")]

    title = models.CharField(max_length=160)
    caption = models.TextField(blank=True)
    channels = models.ManyToManyField(Channel, related_name="posts")
    format = models.CharField(max_length=10, choices=FORMATS, default="photo")
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="posts")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="posts")
    media = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts")
    scheduled_at = models.DateTimeField(null=True, blank=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=14, choices=STATUSES, default=DRAFT, db_index=True)
    published_urls = models.JSONField(default=dict, blank=True)
    reach = models.PositiveIntegerField(default=0)
    engagement = models.PositiveIntegerField(default=0)
    leads = models.PositiveIntegerField(default=0)
    bookings = models.PositiveIntegerField(default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    source = models.CharField(max_length=20, blank=True, help_text="e.g. trip_completed")

    class Meta:
        ordering = ["-scheduled_at", "-pk"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("marketing:post", args=[self.pk])


class InboxMessage(models.Model):
    KINDS = [("message", "Message"), ("comment", "Comment"), ("review", "Review")]
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="inbox")
    external_id = models.CharField(max_length=60, blank=True, db_index=True)
    sender = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    message = models.TextField()
    kind = models.CharField(max_length=8, choices=KINDS, default="message")
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    replied = models.BooleanField(default=False)
    reply_text = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    enquiry = models.ForeignKey("leads.Enquiry", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="inbox_messages")

    class Meta:
        ordering = ["-received_at", "-pk"]
        unique_together = [("channel", "external_id")]


class TrackedLink(TimeStampedModel):
    slug = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    destination = models.CharField(max_length=400)
    campaign = models.ForeignKey(Campaign, null=True, blank=True, on_delete=models.SET_NULL, related_name="links")
    channel = models.ForeignKey(Channel, null=True, blank=True, on_delete=models.SET_NULL, related_name="links")
    medium = models.CharField(max_length=30, default="social")
    content = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("marketing:link", args=[self.pk])

    @property
    def short_path(self) -> str:
        return f"/l/{self.slug}/"

    @property
    def utm(self) -> str:
        from urllib.parse import urlencode

        params = {"utm_source": self.channel.key if self.channel else "direct", "utm_medium": self.medium,
                  "utm_campaign": self.campaign.name.lower().replace(" ", "-") if self.campaign else self.slug}
        if self.content:
            params["utm_content"] = self.content
        sep = "&" if "?" in self.destination else "?"
        return f"{self.destination}{sep}{urlencode(params)}"

    @property
    def click_count(self) -> int:
        return self.clicks.count()


class LinkClick(models.Model):
    link = models.ForeignKey(TrackedLink, on_delete=models.CASCADE, related_name="clicks")
    at = models.DateTimeField(default=timezone.now, db_index=True)
    user_agent = models.CharField(max_length=200, blank=True)
    referrer = models.CharField(max_length=200, blank=True)


class Review(models.Model):
    SOURCES = [("google", "Google"), ("tripadvisor", "TripAdvisor"), ("facebook", "Facebook"), ("website", "Website")]
    source = models.CharField(max_length=12, choices=SOURCES, db_index=True)
    external_id = models.CharField(max_length=60, blank=True, db_index=True)
    guest_name = models.CharField(max_length=120)
    rating = models.PositiveSmallIntegerField(default=5)
    text = models.TextField(blank=True)
    review_date = models.DateField(default=timezone.localdate, db_index=True)
    received_at = models.DateTimeField(default=timezone.now)
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="reviews")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="reviews")
    replied = models.BooleanField(default=False)
    reply_text = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-review_date", "-pk"]
        unique_together = [("source", "external_id")]


class ReviewRequest(models.Model):
    booking = models.ForeignKey("sales.Booking", on_delete=models.CASCADE, related_name="review_requests")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    via = models.CharField(max_length=10, default="whatsapp")
    sent_at = models.DateTimeField(default=timezone.now)
    review = models.ForeignKey(Review, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-sent_at"]
        unique_together = [("booking", "via")]


class AutomationWorkflow(models.Model):
    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True)
    trigger = models.CharField(max_length=60)
    steps = models.JSONField(default=list, blank=True, help_text="[{type: condition|action|wait, text: ...}]")
    enabled = models.BooleanField(default=True)
    run_count = models.PositiveIntegerField(default=0)
    last_run_at = models.DateTimeField(null=True, blank=True)
    template = models.TextField(blank=True, help_text="Message text. {name}, {tour}, {date}, {agent} are filled in.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def ran(self, n=1):
        AutomationWorkflow.objects.filter(pk=self.pk).update(run_count=models.F("run_count") + n,
                                                              last_run_at=timezone.now())


# ---- computed columns used by the list screens
from apps.core.money import format_money  # noqa: E402
Campaign.channel_names = property(lambda self: ", ".join(c.name for c in self.channels.all()) or "No channels")
Campaign.used_label = property(lambda self: f"{self.budget_used}% of budget")
Campaign.enquiry_count = property(lambda self: getattr(self, "_enq", None) if getattr(self, "_enq", None) is not None else self.enquiries.count())
Campaign.cpl_label = property(lambda self: format_money(round(self.spent / self.enquiry_count)) if self.enquiry_count and self.spent else "—")
Post.channel_names = property(lambda self: ", ".join(c.name for c in self.channels.all()))


class ContentShoot(TimeStampedModel):
    """Trip Production: a piece of content moving Sourced → Shot → Posted."""

    SOURCED, SHOT, POSTED = "sourced", "shot", "posted"
    STAGES = [(SOURCED, "Sourced"), (SHOT, "Shot"), (POSTED, "Posted")]
    title = models.CharField(max_length=160)
    stage = models.CharField(max_length=8, choices=STAGES, default=SOURCED, db_index=True)
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="shoots")
    package = models.ForeignKey("catalog.TourPackage", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="shoots")
    assigned_to = models.ForeignKey("people.Employee", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="shoots")
    shoot_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    footage_link = models.CharField(max_length=300, blank=True, help_text="Drive / folder link to the raw footage")
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.SET_NULL, related_name="shoots")
    stage_changed_at = models.DateTimeField(default=timezone.now)
    shot_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["shoot_date", "-pk"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("production:detail", args=[self.pk])

    @property
    def days_in_stage(self) -> int:
        return (timezone.now() - self.stage_changed_at).days

    @property
    def is_overdue(self) -> bool:
        return self.stage == self.SOURCED and bool(self.shoot_date and self.shoot_date < timezone.localdate())


# ---------------------------------------------------------------- website blog reader (Media -> Blogs)
class BlogSource(models.Model):
    """Single row: the company website blog the reader checks every morning."""

    url = models.URLField("blog address", blank=True, help_text="e.g. https://pocketofparadise.co.ke/blog/")
    is_active = models.BooleanField("fetch every day", default=True)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=200, blank=True)
    last_error = models.TextField(blank=True)

    @classmethod
    def load(cls) -> "BlogSource":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"url": getattr(settings, "BLOG_SOURCE_URL", "")})
        return obj


class BlogPost(models.Model):
    """A post found on the company website. One row per post URL (never duplicated)."""

    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    author = models.CharField(max_length=120, blank=True)
    image_url = models.URLField(max_length=500, blank=True)
    categories = models.CharField(max_length=300, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    found_via = models.CharField(max_length=20, blank=True, help_text="wordpress, rss or page")
    fetched_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-fetched_at"]

    def __str__(self):
        return self.title
