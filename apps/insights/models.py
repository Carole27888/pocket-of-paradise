from django.conf import settings
from django.db import models
from django.utils import timezone


class Competitor(models.Model):
    """A company we keep an eye on: its website pages are checked for changes and its Google profile for rating and reviews."""

    name = models.CharField(max_length=120)
    website = models.URLField(blank=True, help_text="Home page, e.g. https://www.competitor.co.ke")
    pages = models.TextField(blank=True, help_text="Other pages to watch, one address per line (safaris, offers, prices)")
    google_query = models.CharField("Google profile", max_length=300, blank=True,
                                    help_text="Paste their Google Maps link, or type the business name and town as Google shows it, e.g. Acme Safaris, Nairobi")
    google_place_id = models.CharField(max_length=120, blank=True, editable=False)
    facebook_url = models.URLField("Facebook", blank=True)
    instagram_url = models.URLField("Instagram", blank=True)
    tiktok_url = models.URLField("TikTok", blank=True)
    youtube_url = models.URLField("YouTube", blank=True)
    is_main = models.BooleanField("Our greatest competitor", default=False, help_text="Shown first, with its own charts")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, help_text="Off: kept, but no longer checked")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-is_main", "name"]

    def __str__(self):
        return self.name

    @property
    def page_list(self) -> list[str]:
        out = [self.website] if self.website else []
        for line in self.pages.splitlines():
            u = line.strip()
            if u.startswith(("http://", "https://")) and u not in out:
                out.append(u)
        return out[:10]

    @property
    def socials(self) -> list[tuple[str, str]]:
        return [(n, u) for n, u in (("Facebook", self.facebook_url), ("Instagram", self.instagram_url), ("TikTok", self.tiktok_url), ("YouTube", self.youtube_url)) if u]

    def latest_google(self):
        return self.snapshots.filter(source=Snapshot.GOOGLE, ok=True).first()


class Snapshot(models.Model):
    """One check of one competitor page or Google profile, and what changed since the check before."""

    WEBSITE, GOOGLE = "website", "google"
    competitor = models.ForeignKey(Competitor, on_delete=models.CASCADE, related_name="snapshots")
    source = models.CharField(max_length=8, choices=[(WEBSITE, "Website"), (GOOGLE, "Google profile")], db_index=True)
    url = models.URLField(max_length=500, blank=True)
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)
    ok = models.BooleanField(default=True)
    error = models.CharField(max_length=300, blank=True)
    title = models.CharField(max_length=300, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    text = models.TextField(blank=True, help_text="The page's readable text, kept to compare with the next check")
    changed = models.BooleanField(default=False, db_index=True)
    added = models.JSONField(default=list, blank=True, help_text="Lines that appeared since the last check")
    removed = models.JSONField(default=list, blank=True, help_text="Lines that disappeared since the last check")
    prices = models.JSONField(default=list, blank=True, help_text="Prices seen on the page")
    rating = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    review_count = models.PositiveIntegerField(null=True, blank=True)
    new_reviews = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-checked_at", "-pk"]
        indexes = [models.Index(fields=["competitor", "source", "url", "-checked_at"])]

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"Could not check: {self.error}"
        if self.source == self.GOOGLE:
            bits = [f"{self.rating}★" if self.rating is not None else "", f"{self.review_count} reviews" if self.review_count is not None else ""]
            new = len(self.new_reviews)
            return " · ".join(b for b in bits if b) + (f" · {new} new review{'s' if new != 1 else ''}" if new else "")
        if not self.changed:
            return "No change"
        return f"{len(self.added)} new line{'s' if len(self.added) != 1 else ''}, {len(self.removed)} removed"


class MarketSearch(models.Model):
    """A question searched on the web by the AI (e.g. the best Mara safaris this season), with its answer and sources."""

    query = models.CharField(max_length=300)
    answer = models.TextField(blank=True)
    sources = models.JSONField(default=list, blank=True)
    provider = models.CharField(max_length=10, blank=True)
    ok = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
