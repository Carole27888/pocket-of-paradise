from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import StyledModelForm

from .models import Accommodation, Destination, Itinerary, ItineraryDay, RoomRate, TourPackage


class DestinationForm(StyledModelForm):
    full_width_fields = ("summary", "description", "activities")

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


class AccommodationForm(StyledModelForm):
    full_width_fields = ("contract_notes",)

    class Meta:
        model = Accommodation
        fields = ["name", "destination", "accommodation_type", "star_rating", "contact_name", "contact_phone",
                  "contact_email", "contract_notes"]


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
