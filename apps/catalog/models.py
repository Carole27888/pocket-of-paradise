from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from apps.core.models import SoftDeleteModel, TimeStampedModel

COUNTRY_CHOICES = [("KE", "Kenya"), ("TZ", "Tanzania"), ("UG", "Uganda"), ("RW", "Rwanda")]


class HasPhotoPack:
    """Tagged photos kept in the photo library, one set per destination or accommodation. Needs a `photo_pack` field and PACK_KIND."""

    PACK_KIND = ""

    def pack_name(self) -> str:
        return self.name

    def ensure_pack(self) -> "PhotoCategory":
        """This record's photo set, created the first time photos are added."""
        if self.photo_pack_id:
            return self.photo_pack
        name = base = self.pack_name()[:90]
        n = 2
        while PhotoCategory.objects.filter(name__iexact=name).exists():
            name, n = f"{base} ({n})"[:100], n + 1
        self.photo_pack = PhotoCategory.objects.create(name=name, kind=self.PACK_KIND, description=f"Photos of {self.name}")
        self.save(update_fields=["photo_pack"])
        return self.photo_pack

    def add_photos(self, files, tags: str = "") -> tuple[int, int]:
        """Save uploaded pictures with the given tags. Returns (added, skipped)."""
        from PIL import Image

        pack, added, skipped = None, 0, 0
        tags = LibraryPhoto.normalize_tags(tags)
        for f in files:
            try:
                Image.open(f).verify()
                f.seek(0)
            except Exception:
                skipped += 1
                continue
            pack = pack or self.ensure_pack()
            LibraryPhoto.objects.create(category=pack, image=f, tags=tags)
            added += 1
        return added, skipped

    @property
    def pack_photos(self):
        return self.photo_pack.images.all() if self.photo_pack_id else LibraryPhoto.objects.none()


class Destination(HasPhotoPack, SoftDeleteModel):
    PACK_KIND = "destination"
    REGION_CHOICES = [
        ("mara", "Maasai Mara"), ("amboseli", "Amboseli"), ("tsavo", "Tsavo"), ("nakuru", "Lake Nakuru"),
        ("naivasha", "Lake Naivasha"), ("coast", "Diani / Coast"), ("mtkenya", "Mt Kenya"),
        ("samburu", "Samburu"), ("northern", "Northern circuit"), ("crossborder", "Cross-border"),
        ("nairobi", "Nairobi"), ("other", "Other"),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    region = models.CharField(max_length=12, choices=REGION_CHOICES, default="other", db_index=True)
    country = models.CharField(max_length=2, choices=COUNTRY_CHOICES, default="KE")
    summary = models.CharField(max_length=220, blank=True)
    description = models.TextField(blank=True)
    cover_photo = models.ImageField(upload_to="destinations/", blank=True)
    best_time_to_visit = models.CharField(max_length=120, blank=True)
    activities = models.CharField(max_length=300, blank=True, help_text="Comma separated")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    is_featured = models.BooleanField(default=False)
    photo_pack = models.OneToOneField("PhotoCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="destination", editable=False,
                                      help_text="This destination's own photos (tagged), picked per day in the itinerary builder.")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:100] or "destination"
            slug, i = base, 2
            while Destination.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, i = f"{base}-{i}", i + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:destination_detail", args=[self.slug])

    @property
    def activity_list(self):
        return [a.strip() for a in self.activities.split(",") if a.strip()]

    @property
    def has_coords(self):
        return self.latitude is not None and self.longitude is not None


class DestinationPhoto(TimeStampedModel):
    destination = models.ForeignKey(Destination, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="destinations/gallery/")
    caption = models.CharField(max_length=160, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]


class Accommodation(HasPhotoPack, SoftDeleteModel):
    PACK_KIND = "accommodation"
    TYPE_CHOICES = [("lodge", "Lodge"), ("camp", "Tented camp"), ("hotel", "Hotel"), ("bnb", "BnB"), ("homestay", "Homestay")]

    name = models.CharField(max_length=120)
    destination = models.ForeignKey(Destination, on_delete=models.PROTECT, related_name="accommodations")
    accommodation_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="lodge")
    star_rating = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    contact_name = models.CharField(max_length=80, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    contract_notes = models.TextField(blank=True)
    photo_pack = models.OneToOneField("PhotoCategory", null=True, blank=True, on_delete=models.SET_NULL, related_name="accommodation", editable=False,
                                      help_text="This accommodation's own photos (tagged). Used for its option in the itinerary builder.")

    class Meta:
        ordering = ["destination__name", "name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("catalog:accommodation_detail", args=[self.pk])

    def pack_name(self) -> str:
        clash = PhotoCategory.objects.filter(name__iexact=self.name).exists()
        return f"{self.name} · {self.destination.name}" if clash else self.name

    @property
    def photos(self):
        return self.pack_photos


class RoomRate(TimeStampedModel):
    SEASONS = [("low", "Low season"), ("shoulder", "Shoulder"), ("high", "High season"), ("peak", "Peak / migration")]
    accommodation = models.ForeignKey(Accommodation, on_delete=models.CASCADE, related_name="rates")
    room_type = models.CharField(max_length=60, default="Double / Twin")
    season = models.CharField(max_length=10, choices=SEASONS, default="high")
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    rate_per_person = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    board_basis = models.CharField(max_length=20, default="Full board")

    class Meta:
        ordering = ["accommodation", "season", "room_type"]

    def __str__(self):
        return f"{self.accommodation} {self.room_type} {self.get_season_display()}"


class Itinerary(TimeStampedModel):
    title = models.CharField(max_length=160)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "itineraries"

    def __str__(self):
        return self.title


class ItineraryDay(models.Model):
    itinerary = models.ForeignKey(Itinerary, on_delete=models.CASCADE, related_name="days")
    day_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    destination = models.ForeignKey(Destination, null=True, blank=True, on_delete=models.SET_NULL)
    accommodation = models.ForeignKey(Accommodation, null=True, blank=True, on_delete=models.SET_NULL)
    breakfast = models.BooleanField(default=False)
    lunch = models.BooleanField(default=False)
    dinner = models.BooleanField(default=False)
    activities = models.CharField(max_length=300, blank=True)
    drive_km = models.PositiveIntegerField(null=True, blank=True)
    drive_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    class Meta:
        ordering = ["day_number"]
        unique_together = [("itinerary", "day_number")]

    def __str__(self):
        return f"Day {self.day_number}: {self.title}"

    @property
    def meals_code(self) -> str:
        return "".join(c for c, f in (("B", self.breakfast), ("L", self.lunch), ("D", self.dinner)) if f)

    @property
    def activity_list(self):
        return [a.strip() for a in self.activities.split(",") if a.strip()]


class TourPackage(SoftDeleteModel):
    TYPE_CHOICES = [("group", "Group joining"), ("private", "Private"), ("custom", "Custom"), ("day", "Day trip")]
    STATUS_CHOICES = [("draft", "Draft"), ("published", "Published"), ("seasonal", "Seasonal"), ("retired", "Retired")]
    DIFFICULTY = [("easy", "Easy"), ("moderate", "Moderate"), ("challenging", "Challenging")]

    code = models.CharField(max_length=12, unique=True, blank=True)
    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True, blank=True)
    destinations = models.ManyToManyField(Destination, related_name="packages")
    package_type = models.CharField(max_length=8, choices=TYPE_CHOICES, default="private", db_index=True)
    days = models.PositiveSmallIntegerField(default=3)
    nights = models.PositiveSmallIntegerField(default=2)
    currency = models.CharField(max_length=3, default="KES")
    price_per_person = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    single_supplement = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    child_rate = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    summary = models.CharField(max_length=240, blank=True)
    inclusions = models.TextField(blank=True, help_text="One per line")
    exclusions = models.TextField(blank=True, help_text="One per line")
    min_group = models.PositiveSmallIntegerField(default=1)
    max_group = models.PositiveSmallIntegerField(default=7)
    difficulty = models.CharField(max_length=12, choices=DIFFICULTY, default="easy")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft", db_index=True)
    cover_photo = models.ImageField(upload_to="packages/", blank=True)
    itinerary = models.OneToOneField(Itinerary, null=True, blank=True, on_delete=models.SET_NULL, related_name="package")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.title}"

    def save(self, *args, **kwargs):
        from apps.core.models import Sequence

        if not self.code:
            self.code = Sequence.next_number("package", "PKG", 4)
        if not self.slug:
            base = slugify(self.title)[:160] or "package"
            slug, i = base, 2
            while TourPackage.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, i = f"{base}-{i}", i + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("catalog:package_detail", args=[self.slug])

    @property
    def duration_label(self):
        return f"{self.days} days / {self.nights} nights" if self.nights else f"{self.days} day"

    @property
    def inclusion_list(self):
        return [x.strip("-• ").strip() for x in self.inclusions.splitlines() if x.strip()]

    @property
    def exclusion_list(self):
        return [x.strip("-• ").strip() for x in self.exclusions.splitlines() if x.strip()]


# ------------------------------------------------------------------------------------------------ photo library
# Ported from the standalone Itinerary Builder: a pack of tagged photos per destination or lodge, picked per day.
import re  # noqa: E402
from io import BytesIO  # noqa: E402

from django.core.files.base import ContentFile  # noqa: E402

MAX_IMAGE_SIDE = 2000


class PhotoCategory(TimeStampedModel):
    """A pack of photos for one destination or one lodge/hotel (e.g. Maasai Mara, Mara Serena)."""

    KIND_CHOICES = [("destination", "Destination"), ("accommodation", "Accommodation")]
    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="destination", db_index=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "photo categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:110] or "pack"
            slug, n = base, 2
            while PhotoCategory.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def cover(self):
        return self.images.first()


class LibraryPhoto(TimeStampedModel):
    category = models.ForeignKey(PhotoCategory, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="gallery/%Y/%m/")
    thumb = models.ImageField(upload_to="thumbs/%Y/%m/", blank=True)
    caption = models.CharField(max_length=200, blank=True)
    tags = models.CharField(max_length=300, blank=True, help_text="Comma separated, e.g. lion, sunset, tent.")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.caption or f"{self.category} #{self.pk}"

    @staticmethod
    def normalize_tags(raw):
        seen = []
        for t in re.split(r"[,;\n]+", raw or ""):
            t = " ".join(t.lower().split())
            if t and t not in seen:
                seen.append(t)
        return ", ".join(seen)

    @property
    def tag_list(self):
        return [t for t in self.tags.split(", ") if t]

    @property
    def thumb_url(self):
        return (self.thumb or self.image).url

    def save(self, *args, **kwargs):
        self.tags = self.normalize_tags(self.tags)
        if self.image and not self.image._committed:
            self._shrink()
        super().save(*args, **kwargs)

    def _shrink(self):
        """Phone photos are huge; shrink new uploads so PDFs stay small enough to email."""
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(self.image))
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        stem = self.image.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self.image.save(f"{stem}.jpg", ContentFile(buf.getvalue()), save=False)
        small = img.copy()
        small.thumbnail((420, 420))
        tb = BytesIO()
        small.save(tb, "JPEG", quality=78, optimize=True)
        self.thumb.save(f"{stem}.jpg", ContentFile(tb.getvalue()), save=False)
