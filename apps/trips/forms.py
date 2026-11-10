from datetime import timedelta

from django import forms

from apps.catalog.models import TourPackage
from apps.core.forms import StyledForm, StyledModelForm
from apps.fleet.models import Vehicle
from apps.people.models import Employee

from .models import CHECKLIST, Departure, Incident, ManifestEntry


class DepartureForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Departure
        fields = ["package", "start_date", "end_date", "capacity", "meeting_point", "pickup_time", "notes"]
        widgets = {"start_date": forms.DateInput(), "end_date": forms.DateInput(),
                   "pickup_time": forms.TimeInput(attrs={"type": "time"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["package"].queryset = TourPackage.objects.alive().exclude(status="retired")
        self.fields["end_date"].required = False
        self.fields["end_date"].help_text = "Leave empty to use the package length."

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]:
            self.add_error("end_date", "The trip can't end before it starts.")
        if self.instance.pk and d.get("capacity") and d["capacity"] < self.instance.seats_sold:
            self.add_error("capacity", f"{self.instance.seats_sold} seats are already sold.")
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.end_date:
            obj.end_date = obj.start_date + timedelta(days=max(obj.package.days - 1, 0))
        if commit:
            obj.save()
        return obj


class AssignResourcesForm(StyledForm):
    guide = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    driver = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False)
    vehicle = forms.ModelChoiceField(queryset=Vehicle.objects.none(), required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        crew = Employee.objects.filter(is_active=True)
        self.fields["guide"].queryset = crew.filter(is_guide=True)
        self.fields["driver"].queryset = crew.filter(is_driver=True)
        self.fields["vehicle"].queryset = Vehicle.objects.exclude(status=Vehicle.OUT_OF_SERVICE)


class ChecklistForm(StyledForm):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for field, label in CHECKLIST:
            self.fields[field] = forms.BooleanField(required=False, label=label)
            self.fields[field].widget.attrs["class"] = "check"


class IncidentForm(StyledModelForm):
    full_width_fields = ("description", "action_taken")

    class Meta:
        model = Incident
        fields = ["kind", "severity", "description", "action_taken"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "action_taken": forms.Textarea(attrs={"rows": 2})}


class ManifestEntryForm(StyledModelForm):
    class Meta:
        model = ManifestEntry
        fields = ["full_name", "is_child", "age", "nationality", "passport_number", "phone", "dietary_notes",
                  "medical_notes", "emergency_contact", "pickup_point"]


class TripExpenseForm(StyledForm):
    full_width_fields = ("description",)
    category = forms.ChoiceField(choices=[("fuel", "Fuel"), ("park_fees", "Park entry fees"), ("tips", "Tips & incidentals"),
                                          ("catering", "Meals"), ("maintenance", "Repairs on the road"), ("other", "Other")])
    amount = forms.DecimalField(min_value=1, max_digits=14, decimal_places=2, label="Amount (KES)")
    method = forms.ChoiceField(choices=[("cash", "Cash"), ("mpesa", "M-PESA"), ("card", "Card")], initial="cash")
    description = forms.CharField(max_length=200, label="What was it for?")
    receipt = forms.FileField(required=False, help_text="Photo of the receipt: expenses without one are flagged Missing")


class DelayForm(StyledForm):
    reason = forms.CharField(required=False, max_length=200, label="Delay reason")


class CancelForm(StyledForm):
    reason = forms.CharField(max_length=200)
