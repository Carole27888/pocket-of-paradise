from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import StyledModelForm
from apps.core.widgets import StarRatingInput

from .models import Accommodation, Destination, Itinerary, ItineraryDay, RoomRate, TourPackage


class MultipleImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.FileField):
    """Any number of pictures in one go."""

    def __init__(self, *a, **kw):
        kw.setdefault("widget", MultipleImageInput(attrs={"accept": "image/*", "multiple": True}))
        super().__init__(*a, **kw)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class PhotoUploadMixin:
    """Adds "Photos" (any number at once) and "Tags for these photos" to a destination or accommodation form."""

    def save(self, commit=True):
        obj = super().save(commit)
        files = self.cleaned_data.get("photos") or []
        if files and commit:
            self.photos_added, self.photos_skipped = obj.add_photos(files, self.cleaned_data.get("photo_tags", ""))
        return obj


def _photo_fields():
    return (MultipleImageField(required=False, label="Photos", help_text="Choose as many pictures as you like at once. JPG, PNG or WebP."),
            forms.CharField(required=False, max_length=300, label="Tags for these photos", widget=forms.TextInput(attrs={"placeholder": "e.g. lion, sunset, beach"}),
                            help_text="Comma separated. They help find the right picture later; you can change tags per photo afterwards."))


class DestinationForm(PhotoUploadMixin, StyledModelForm):
    full_width_fields = ("summary", "description", "activities", "photos", "photo_tags")
    photos, photo_tags = _photo_fields()

    class Meta:
        model = Destination
        fields = ["name", "region", "country", "best_time_to_visit", "latitude", "longitude", "is_featured",
                  "cover_photo", "summary", "activities", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}


class PackageForm(StyledModelForm):
    full_width_fields = ("summary", "destinations", "inclusions", "exclusions")

    class Meta:
        model = TourPackage
        fields = ["title", "package_type", "status", "days", "nights", "currency", "price_per_person",
                  "single_supplement", "child_rate", "min_group", "max_group", "difficulty", "cover_photo",
                  "summary", "destinations", "inclusions", "exclusions"]
        widgets = {"destinations": forms.CheckboxSelectMultiple, "inclusions": forms.Textarea(attrs={"rows": 5}),
                   "exclusions": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["destinations"].queryset = Destination.objects.alive()
        self.fields["currency"].widget = forms.Select(
            choices=[("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")], attrs={"class": "input select"})


class AccommodationForm(PhotoUploadMixin, StyledModelForm):
    full_width_fields = ("contract_notes", "photos", "photo_tags")
    photos, photo_tags = _photo_fields()

    class Meta:
        model = Accommodation
        fields = ["name", "destination", "accommodation_type", "star_rating", "contact_name", "contact_phone",
                  "contact_email", "contract_notes"]
        widgets = {"star_rating": StarRatingInput()}


class ItineraryDayForm(StyledModelForm):
    class Meta:
        model = ItineraryDay
        fields = ["day_number", "title", "destination", "accommodation", "breakfast", "lunch", "dinner",
                  "activities", "drive_km", "drive_hours", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


ItineraryDayFormSet = inlineformset_factory(Itinerary, ItineraryDay, form=ItineraryDayForm, extra=1, can_delete=True)
RoomRateFormSet = inlineformset_factory(
    Accommodation, RoomRate, extra=1, can_delete=True,
    fields=["room_type", "season", "board_basis", "rate_per_person", "currency", "valid_from", "valid_to"],
    widgets={"valid_from": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
             "valid_to": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
             "season": forms.Select(attrs={"class": "input select"})},
)
