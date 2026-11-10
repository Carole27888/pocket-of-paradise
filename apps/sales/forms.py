from django import forms
from django.forms import inlineformset_factory
from datetime import timedelta

from django.utils import timezone

from apps.catalog.models import Destination
from apps.core.forms import StyledForm, StyledModelForm
from apps.core.widgets import DropzoneInput
from apps.leads.models import Purpose, Source, source_choices
from apps.people.models import Employee

from .models import (AssignmentRule, Booking, BookingPayment, BookingStatus, CallLog, FollowUp, LeadItinerary,
                     ItineraryHotel, ItineraryPriceLine, ItineraryTravel, LeadItineraryDay, Quotation, QuotationItem, TemplatePriceLine, SalesSettings, SalesTarget, ItineraryTemplate, TemplateDay)

DT_FORMAT = "%Y-%m-%dT%H:%M"


def dt_widget():
    return forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT_FORMAT)


def agent_queryset():
    return Employee.objects.filter(is_sales_agent=True, is_active=True)


class EnquiryPickMixin:
    """Adds an `enquiry` picker limited to the enquiries the user may act on (hidden when preset)."""

    def add_enquiry_field(self, enquiries, preset=None):
        from apps.leads.models import Enquiry

        field = forms.ModelChoiceField(queryset=enquiries if enquiries is not None else Enquiry.objects.none(),
                                       label="Enquiry")
        field.label_from_instance = lambda e: f"{e.reference} · {e.client.full_name} · {e.interest_label}"
        self.fields = {"enquiry": field, **self.fields}
        field.widget.attrs.update({"class": "input select", "required": True})
        if preset is not None:
            self.fields["enquiry"].initial = preset.pk
            self.fields["enquiry"].widget = forms.HiddenInput()


class CallEditForm(StyledForm):
    """Correct a logged call: what was said, how it went, when and for how long."""
    full_width_fields = ("notes",)
    direction = forms.ChoiceField(choices=CallLog.DIRECTIONS)
    outcome = forms.ChoiceField(choices=CallLog.OUTCOMES)
    minutes = forms.IntegerField(min_value=0, max_value=600, required=False, label="Duration (minutes)")
    called_at = forms.DateTimeField(label="When", widget=dt_widget(), input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"])
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}), label="What was said")


class CallForm(EnquiryPickMixin, StyledForm):
    full_width_fields = ("notes",)
    direction = forms.ChoiceField(choices=CallLog.DIRECTIONS, initial="out")
    outcome = forms.ChoiceField(choices=CallLog.OUTCOMES)
    minutes = forms.IntegerField(min_value=0, max_value=600, required=False, label="Duration (minutes)", initial=0)
    called_at = forms.DateTimeField(label="When", widget=dt_widget(), input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"])
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                            label="What was said")
    follow_up_at = forms.DateTimeField(required=False, label="Follow up on", widget=dt_widget(),
                                       input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"],
                                       help_text="Optional: creates a follow-up task.")
    follow_up_title = forms.CharField(required=False, max_length=160, label="Follow-up reminder")

    def __init__(self, *a, enquiries=None, preset=None, **kw):
        super().__init__(*a, **kw)
        self.add_enquiry_field(enquiries, preset)
        self.fields["called_at"].initial = timezone.localtime().replace(second=0, microsecond=0)
        self.fields["called_at"].widget.attrs["max"] = timezone.localtime().strftime(DT_FORMAT)

    def clean_called_at(self):
        when = self.cleaned_data["called_at"]
        if when > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError("A call can't be in the future.")
        return when


class FollowUpForm(EnquiryPickMixin, StyledForm):
    kind = forms.ChoiceField(choices=FollowUp.KINDS)
    title = forms.CharField(max_length=160, label="What needs doing")
    due_at = forms.DateTimeField(label="Due", widget=dt_widget(), input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"])

    def __init__(self, *a, enquiries=None, preset=None, **kw):
        super().__init__(*a, **kw)
        self.add_enquiry_field(enquiries, preset)
        self.fields["due_at"].initial = (timezone.localtime() + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)


class QuotationForm(StyledModelForm):
    full_width_fields = ("title", "intro", "terms")

    class Meta:
        model = Quotation
        fields = ["title", "currency", "valid_until", "discount", "vat_rate", "intro", "terms"]
        widgets = {"valid_until": forms.DateInput(), "intro": forms.Textarea(attrs={"rows": 3}),
                   "terms": forms.Textarea(attrs={"rows": 5})}

    def clean_discount(self):
        d = self.cleaned_data.get("discount") or 0
        if d < 0:
            raise forms.ValidationError("Discount can't be negative.")
        return d

    def clean_valid_until(self):
        v = self.cleaned_data.get("valid_until")
        if v and v < timezone.localdate():
            raise forms.ValidationError("Choose a date that is not in the past.")
        return v


class QuotationItemForm(StyledModelForm):
    class Meta:
        model = QuotationItem
        fields = ["kind", "description", "quantity", "unit_price"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.5", "min": "0"}),
                   "unit_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}

    def clean(self):
        d = super().clean()
        if d.get("quantity") is not None and d["quantity"] <= 0:
            self.add_error("quantity", "Quantity must be above zero.")
        return d


QuotationItemFormSet = inlineformset_factory(Quotation, QuotationItem, form=QuotationItemForm, extra=0,
                                             can_delete=True, min_num=1, validate_min=True)


class SendForm(StyledForm):
    via = forms.ChoiceField(choices=[("whatsapp", "WhatsApp"), ("email", "E-mail"), ("print", "Handed over / printed")])


class DecisionForm(StyledForm):
    note = forms.CharField(required=False, max_length=200, label="Reason / note")


class AcceptForm(StyledForm):
    travel_start = forms.DateField(required=False, label="Travel from")
    travel_end = forms.DateField(required=False, label="Travel to")
    deposit_percent = forms.IntegerField(min_value=0, max_value=100, initial=30, label="Deposit %")

    def clean(self):
        d = super().clean()
        if d.get("travel_start") and d.get("travel_end") and d["travel_end"] < d["travel_start"]:
            self.add_error("travel_end", "Travel can't end before it starts.")
        return d


class PaymentForm(StyledForm):
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2)
    method = forms.ChoiceField(choices=BookingPayment.METHODS)
    reference = forms.CharField(required=False, max_length=60, label="Transaction code")
    received_at = forms.DateTimeField(required=False, label="Received", widget=dt_widget(),
                                      input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"])
    note = forms.CharField(required=False, max_length=160)


class BookingStatusForm(StyledForm):
    status = forms.ChoiceField(choices=BookingStatus.choices)
    reason = forms.CharField(required=False, max_length=200)

    def clean(self):
        d = super().clean()
        if d.get("status") == BookingStatus.CANCELLED and not d.get("reason"):
            self.add_error("reason", "Tell us why the booking was cancelled.")
        return d


class BookingEditForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Booking
        fields = ["travel_start", "travel_end", "adults", "children", "deposit_percent", "notes"]
        widgets = {"travel_start": forms.DateInput(), "travel_end": forms.DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        d = super().clean()
        if d.get("travel_start") and d.get("travel_end") and d["travel_end"] < d["travel_start"]:
            self.add_error("travel_end", "Travel can't end before it starts.")
        return d


class ReassignForm(StyledForm):
    agent = forms.ModelChoiceField(queryset=Employee.objects.none())

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["agent"].queryset = agent_queryset()


class AssignmentRuleForm(StyledModelForm):
    class Meta:
        model = AssignmentRule
        fields = ["name", "priority", "source", "purpose", "destination", "international_only", "agent", "active"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["source"] = forms.ChoiceField(choices=[("", "Any channel")] + source_choices(), required=False,
                                                  help_text=self.fields["source"].help_text)
        self.fields["purpose"] = forms.ChoiceField(choices=[("", "Any purpose")] + list(Purpose.choices),
                                                   required=False, help_text=self.fields["purpose"].help_text)
        for f in ("source", "purpose"):
            self.fields[f].widget.attrs["class"] = "input select"
        self.fields["destination"].queryset = Destination.objects.alive()
        self.fields["destination"].empty_label = "Any destination"
        self.fields["agent"].queryset = agent_queryset()


class SalesSettingsForm(StyledModelForm):
    class Meta:
        model = SalesSettings
        fields = ["response_sla_minutes", "auto_assign_new", "route_by_specialty", "quote_valid_days", "vat_rate", "follow_up_stale_days", "min_margin_percent"]


class AutomationForm(StyledModelForm):
    class Meta:
        model = SalesSettings
        fields = ["automation_on", "auto_quote_chase_days", "auto_reconnect_days", "auto_deposit_chase_days", "auto_balance_chase_days",
                  "auto_pretrip_days", "auto_feedback_after_days", "auto_callback_after_call", "auto_plan_reminders"]


class TargetForm(StyledModelForm):
    class Meta:
        model = SalesTarget
        fields = ["agent", "year", "month", "revenue_target", "bookings_target", "calls_target"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        today = timezone.localdate()
        self.fields["agent"].queryset = agent_queryset()
        self.fields["year"].initial = today.year
        self.fields["month"] = forms.TypedChoiceField(
            coerce=int, choices=[(i, timezone.datetime(2000, i, 1).strftime("%B")) for i in range(1, 13)],
            initial=today.month)
        self.fields["month"].widget.attrs["class"] = "input select"

    def validate_unique(self):
        pass  # same agent+month updates the existing target (see the view)


class ItineraryStartForm(EnquiryPickMixin, StyledForm):
    """One "Start from" choice: a blank itinerary, one of the team's templates, or a catalogue package's itinerary.
    `clean()` splits it into `template` / `package` for the view."""

    start = forms.ChoiceField(label="Start from", required=False)
    title = forms.CharField(required=False, max_length=160, label="Title (optional)")

    def __init__(self, *a, enquiries=None, preset=None, **kw):
        super().__init__(*a, **kw)
        from apps.catalog.models import TourPackage

        if enquiries is not None or preset is not None:
            self.add_enquiry_field(enquiries, preset)
        # the title follows the chosen lead ("Client · what they asked for") until the agent types their own
        if preset is not None:
            self.fields["title"].initial = f"{preset.client.full_name} · {preset.interest_label}"
        else:
            self.fields["enquiry"].widget.attrs["data-autotitle-source"] = "1"
        self.fields["title"].widget.attrs["data-autotitle-target"] = "1"
        self.fields["title"].help_text = "Filled in from the lead; change it if you like."
        self._templates = {t.pk: t for t in ItineraryTemplate.objects.all()}
        self._packages = {p.pk: p for p in TourPackage.objects.alive().exclude(status="retired").filter(itinerary__isnull=False)}
        self.fields["start"].choices = [("", "Blank itinerary (add days yourself)"),
                                        ("Team templates", [(f"t:{t.pk}", f"{t.title} · {t.day_count} days") for t in self._templates.values()]),
                                        ("Package itineraries from the catalogue", [(f"p:{p.pk}", f"{p.title} · {p.days} days") for p in self._packages.values()])]
        init = self.initial
        if init.get("template"):
            self.fields["start"].initial = f"t:{init['template']}"
        elif init.get("package"):
            self.fields["start"].initial = f"p:{init['package']}"
        self.fields["start"].help_text = "A template is a trip your team built and saved. A package itinerary is the day-by-day plan attached to a catalogue package."

    def clean(self):
        d = super().clean()
        start = d.get("start") or ""
        d["template"] = self._templates.get(int(start[2:])) if start.startswith("t:") and start[2:].isdigit() else None
        d["package"] = self._packages.get(int(start[2:])) if start.startswith("p:") and start[2:].isdigit() else None
        return d


class ItineraryForm(StyledModelForm):
    full_width_fields = ("title", "intro", "inclusions", "exclusions", "terms")

    class Meta:
        model = LeadItinerary
        fields = ["title", "start_date", "currency", "margin_percent", "photo_pack", "nights_override", "intro", "inclusions", "exclusions", "terms"]
        widgets = {"start_date": forms.DateInput(), "intro": forms.Textarea(attrs={"rows": 2}), "inclusions": forms.Textarea(attrs={"rows": 5}),
                   "exclusions": forms.Textarea(attrs={"rows": 5}), "terms": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.catalog.models import PhotoCategory

        self.fields["photo_pack"].queryset = PhotoCategory.objects.filter(kind="destination")
        self.fields["photo_pack"].empty_label = "No photo pack"
        # an itinerary with no terms yet starts from the standard ones Operations has set
        if not (self.instance and self.instance.terms):
            from .services import standard_terms

            self.initial["terms"] = standard_terms()
        self.fields["terms"].help_text = "Filled in from the standard terms set by Operations; edit them for this client if you need to."

    def clean_margin_percent(self):
        m = self.cleaned_data["margin_percent"]
        if m < 0 or m > 300:
            raise forms.ValidationError("Use a margin between 0 and 300%.")
        return m


class ItineraryDayForm(StyledModelForm):
    """One day. `day_number` follows the on-screen order (the builder renumbers it when days are dragged); `copy_image_from`
    ("t:<template day id>") lets a day borrowed from a template bring its picture along."""

    copy_image_from = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_image(self):
        img = self.cleaned_data.get("image")
        if img and hasattr(img, "size") and img.size > 5 * 1024 * 1024:
            raise forms.ValidationError("That picture is over 5 MB. Please use a smaller one.")
        return img

    class Meta:
        model = LeadItineraryDay
        fields = ["day_number", "title", "description", "destination", "accommodation", "room_type", "breakfast", "lunch", "dinner",
                  "activities", "drive_km", "drive_hours", "lodging_pp", "activities_pp", "transport", "image", "photos"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2}), "day_number": forms.HiddenInput, "photos": forms.MultipleHiddenInput,
                   "image": DropzoneInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
                   "lodging_pp": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
                   "activities_pp": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
                   "transport": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.catalog.models import Accommodation, Destination

        self.fields["destination"].queryset = Destination.objects.alive()
        self.fields["accommodation"].queryset = Accommodation.objects.alive().select_related("destination")
        self.fields["day_number"].required = False
        if "photos" in self.fields:
            from apps.catalog.models import LibraryPhoto

            self.fields["photos"].required = False
            self.fields["photos"].queryset = LibraryPhoto.objects.all()
        for f in ("lodging_pp", "activities_pp", "transport"):
            self.fields[f].required = False

    def clean(self):
        d = super().clean()
        for f in ("lodging_pp", "activities_pp", "transport"):
            if d.get(f) is None:
                d[f] = 0
        if d.get("day_number") is None:
            d["day_number"] = self.instance.day_number if self.instance.pk else 999   # a new day with no position goes last
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        src = (self.cleaned_data.get("copy_image_from") or "").strip()
        if src.startswith("t:") and src[2:].isdigit() and not self.cleaned_data.get("image") and not obj.image:
            from .models import TemplateDay
            from .services import copy_day_image

            donor = TemplateDay.objects.filter(pk=int(src[2:])).first()
            if donor:
                copy_day_image(donor, obj)
        if commit:
            obj.save()
            self._save_m2m()
        return obj


def _selected_photos(form):
    """Photos currently chosen for a day (posted values win over saved ones), for the thumbnails in the builder."""
    from apps.catalog.models import LibraryPhoto

    if form.is_bound:
        ids = [i for i in form.data.getlist(form.add_prefix("photos")) if str(i).isdigit()]
        by_id = LibraryPhoto.objects.in_bulk(ids)
        return [by_id[int(i)] for i in ids if int(i) in by_id]
    return list(form.instance.photos.all()) if form.instance.pk else []


ItineraryDayForm.selected_photos = property(_selected_photos)


class PriceLineForm(StyledModelForm):
    class Meta:
        model = ItineraryPriceLine
        fields = ["description", "quantity", "unit_price"]
        widgets = {"description": forms.TextInput(attrs={"placeholder": "e.g. Park fees, flights, visas"}), "quantity": forms.NumberInput(attrs={"min": 1}),
                   "unit_price": forms.NumberInput(attrs={"min": 0, "step": "0.01"})}


class HotelForm(StyledModelForm):
    class Meta:
        model = ItineraryHotel
        fields = ["photo_pack", "name", "meal_plan", "room_type", "price_per_person"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Lodge name (defaults to the photo pack)"}),
                   "room_type": forms.TextInput(attrs={"placeholder": "e.g. Garden view double"}), "price_per_person": forms.NumberInput(attrs={"min": 0, "step": "0.01"})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.catalog.models import PhotoCategory

        self.fields["photo_pack"].queryset = PhotoCategory.objects.filter(kind="accommodation")
        self.fields["photo_pack"].empty_label = "Lodge photos…"

    def clean(self):
        d = super().clean()
        if not d.get("name") and d.get("photo_pack"):
            d["name"] = d["photo_pack"].name
        if not d.get("name") and not self.errors and self.has_changed():
            self.add_error("name", "Give the lodge a name or pick its photo pack.")
        return d


class TemplateDayForm(ItineraryDayForm):
    class Meta(ItineraryDayForm.Meta):
        model = TemplateDay
        fields = [f for f in ItineraryDayForm.Meta.fields if f != "photos"]


class TemplateForm(StyledModelForm):
    full_width_fields = ("title", "description", "intro", "inclusions", "exclusions", "terms")

    class Meta:
        model = ItineraryTemplate
        fields = ["title", "description", "intro", "inclusions", "exclusions", "terms"]
        widgets = {"intro": forms.Textarea(attrs={"rows": 2}), "inclusions": forms.Textarea(attrs={"rows": 4}), "exclusions": forms.Textarea(attrs={"rows": 4}),
                   "terms": forms.Textarea(attrs={"rows": 3})}


ItineraryDayFormSet = inlineformset_factory(LeadItinerary, LeadItineraryDay, form=ItineraryDayForm, extra=0,
                                            can_delete=True, min_num=1, validate_min=True)


TemplateDayFormSet = inlineformset_factory(ItineraryTemplate, TemplateDay, form=TemplateDayForm, extra=0, can_delete=True, min_num=1, validate_min=True)


class TravelForm(StyledModelForm):
    class Meta:
        model = ItineraryTravel
        fields = ["mode", "name", "basis", "price", "notes"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Noah with pop-up roof (defaults to the type)"}),
                   "notes": forms.TextInput(attrs={"placeholder": "e.g. 7 seats, driver-guide and fuel included"}),
                   "price": forms.NumberInput(attrs={"min": 0, "step": "0.01"})}

    def clean(self):
        d = super().clean()
        if not d.get("name") and d.get("mode"):
            d["name"] = dict(ItineraryTravel.MODES).get(d["mode"], "")
        return d


TravelFormSet = inlineformset_factory(LeadItinerary, ItineraryTravel, form=TravelForm, extra=0, can_delete=True)
PriceLineFormSet = inlineformset_factory(LeadItinerary, ItineraryPriceLine, form=PriceLineForm, extra=0, can_delete=True)
HotelFormSet = inlineformset_factory(LeadItinerary, ItineraryHotel, form=HotelForm, extra=0, can_delete=True)
TemplatePriceFormSet = inlineformset_factory(ItineraryTemplate, TemplatePriceLine, form=PriceLineForm, extra=0, can_delete=True)
