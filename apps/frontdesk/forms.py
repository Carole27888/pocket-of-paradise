from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.catalog.models import Destination, TourPackage
from apps.core.forms import StyledForm, StyledModelForm
from apps.fleet.models import Vehicle
from apps.leads.models import Enquiry, Purpose, Stage
from apps.people.models import Employee

from . import services
from .models import DESK_SOURCES, FUEL_LEVELS, TripMovement


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%dT%H:%M")


def _local_now():
    return timezone.localtime().replace(second=0, microsecond=0)


class CheckOutForm(StyledForm):
    full_width_fields = ("notes_out",)
    vehicle = forms.ModelChoiceField(queryset=Vehicle.objects.none(), required=False,
                                     help_text="Only vehicles marked available are listed.")
    driver = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    guide = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False,
                                   help_text="Leave empty when the driver is also the guide.")
    purpose = forms.ChoiceField(choices=TripMovement.PURPOSES)
    destination = forms.ModelChoiceField(queryset=Destination.objects.none(), required=False)
    destination_text = forms.CharField(label="Or describe where", max_length=160, required=False,
                                       help_text="e.g. JKIA pick-up, Karen garage")
    enquiry = forms.ModelChoiceField(queryset=Enquiry.objects.none(), required=False, label="Linked booking",
                                     help_text="Deposit-paid and confirmed enquiries.")
    passengers = forms.IntegerField(min_value=0, initial=0)
    checked_out_at = forms.DateTimeField(label="Leaving at", widget=DateTimeInput)
    expected_return_at = forms.DateTimeField(label="Expected back", widget=DateTimeInput)
    odometer_out = forms.IntegerField(label="Odometer (km)", min_value=0, required=False,
                                      help_text="Defaults to the vehicle's last recorded reading.")
    fuel_out = forms.ChoiceField(label="Fuel level", choices=[("", "—")] + FUEL_LEVELS, required=False)
    notes_out = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        driver_ids = [e.pk for e in services.available_crew("driver")]
        guide_ids = [e.pk for e in services.available_crew("guide")]
        self.fields["vehicle"].queryset = Vehicle.objects.filter(status=Vehicle.AVAILABLE).select_related("current_driver")
        self.fields["vehicle"].label_from_instance = lambda v: f"{v} · {v.seats} seats"
        self.fields["driver"].queryset = Employee.objects.filter(pk__in=driver_ids)
        self.fields["guide"].queryset = Employee.objects.filter(pk__in=guide_ids)
        self.fields["destination"].queryset = Destination.objects.alive()
        self.fields["enquiry"].queryset = (Enquiry.objects.filter(stage__in=[Stage.DEPOSIT, Stage.CONFIRMED])
                                           .select_related("client").order_by("travel_date_from", "-created_at"))
        self.fields["enquiry"].label_from_instance = lambda e: f"{e.reference} · {e.client.full_name} · {e.interest_label}"
        now = _local_now()
        self.fields["checked_out_at"].initial = now
        self.fields["expected_return_at"].initial = (now + timedelta(hours=8)).replace(minute=0)

    def clean(self):
        d = super().clean()
        if not (d.get("vehicle") or d.get("driver") or d.get("guide")):
            raise forms.ValidationError("Choose a vehicle, a driver or a guide to check out.")
        out, back = d.get("checked_out_at"), d.get("expected_return_at")
        if out and back and back <= out:
            self.add_error("expected_return_at", "Must be after the time they leave.")
        if out and out > timezone.now() + timedelta(minutes=5):
            self.add_error("checked_out_at", "Can't check out in the future; record it when they leave.")
        v = d.get("vehicle")
        if v and d.get("passengers") and d["passengers"] > v.seats:
            self.add_error("passengers", f"{v.registration_no} has {v.seats} seats.")
        return d


class CheckInForm(StyledForm):
    full_width_fields = ("notes_in",)
    checked_in_at = forms.DateTimeField(label="Returned at", widget=DateTimeInput)
    odometer_in = forms.IntegerField(label="Odometer (km)", min_value=0, required=False)
    fuel_in = forms.ChoiceField(label="Fuel level", choices=[("", "—")] + FUEL_LEVELS, required=False)
    condition_in = forms.ChoiceField(label="Vehicle condition", choices=TripMovement.CONDITIONS,
                                     initial=TripMovement.CONDITION_OK)
    notes_in = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                               help_text="Damage, incidents, lost property, client feedback.")

    def __init__(self, *a, movement=None, **kw):
        super().__init__(*a, **kw)
        self.movement = movement
        self.fields["checked_in_at"].initial = _local_now()
        if movement is not None and not movement.vehicle_id:
            for f in ("odometer_in", "fuel_in", "condition_in"):
                del self.fields[f]
        elif movement is not None:
            self.fields["odometer_in"].required = True
            self.fields["odometer_in"].widget.attrs["required"] = True
            if movement.odometer_out is not None:
                self.fields["odometer_in"].help_text = f"At check-out: {movement.odometer_out:,} km"
                self.fields["odometer_in"].widget.attrs["min"] = movement.odometer_out

    def clean(self):
        d = super().clean()
        mv = self.movement
        if mv is None:
            return d
        when = d.get("checked_in_at")
        if when and when < mv.checked_out_at:
            self.add_error("checked_in_at", "Can't be before the check-out time.")
        if when and when > timezone.now() + timedelta(minutes=5):
            self.add_error("checked_in_at", "Can't be in the future.")
        odo = d.get("odometer_in")
        if odo is not None and mv.odometer_out is not None and odo < mv.odometer_out:
            self.add_error("odometer_in", f"Lower than at check-out ({mv.odometer_out:,} km).")
        return d


class QuickCaptureForm(StyledForm):
    """The short capture form on each queue. The full enquiry form stays available for detail."""

    full_width_fields = ("notes",)
    source = forms.ChoiceField(choices=[])
    full_name = forms.CharField(label="Client name", max_length=120)
    phone = forms.CharField(label="Phone number", max_length=30)
    package = forms.ModelChoiceField(queryset=TourPackage.objects.none(), required=False, label="Package of interest")
    destination_text = forms.CharField(label="Or interest", max_length=160, required=False,
                                       help_text="e.g. Mara 3 days in August")
    purpose = forms.ChoiceField(choices=Purpose.choices, initial=Purpose.NEW)
    adults = forms.IntegerField(min_value=1, initial=2)
    children = forms.IntegerField(min_value=0, initial=0)
    assigned_agent = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False, label="Assign to",
                                            empty_label="Assign later (waiting queue)")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.leads.models import Source

        labels = dict(Source.choices)
        self.fields["source"].choices = [(s, labels[s]) for s in DESK_SOURCES]
        self.fields["package"].queryset = TourPackage.objects.alive().exclude(status="retired")
        agents = [a.pk for a in Employee.objects.filter(is_sales_agent=True, is_active=True) if a.is_available]
        self.fields["assigned_agent"].queryset = Employee.objects.filter(pk__in=agents)
        self.fields["phone"].widget.input_type = "tel"
        self.fields["phone"].widget.attrs.update(autocomplete="tel", placeholder="0712 345 678 or +44…")



class BusinessCardForm(StyledModelForm):
    full_width_fields = ("notes", "card_image")

    class Meta:
        from apps.core.widgets import DropzoneInput

        from .models import BusinessCard

        model = BusinessCard
        fields = ["name", "email", "phone", "category", "company", "job_title", "notes", "card_image", "marketing_opt_out"]
        widgets = {"card_image": DropzoneInput(attrs={"accept": "image/jpeg,image/png,image/webp"}, label="Drag a photo of the card here or click to choose")}

    def clean(self):
        d = super().clean()
        if not d.get("email") and not d.get("phone"):
            raise forms.ValidationError("Enter at least an e-mail address or a phone number.")
        return d
