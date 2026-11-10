from apps.core.forms import StyledModelForm
from apps.people.models import Employee

from .models import FuelLog, MaintenanceLog, Vehicle


class VehicleForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Vehicle
        fields = ["registration_no", "nickname", "vehicle_type", "make_model", "year", "seats", "status",
                  "current_driver", "odometer_km", "insurance_expiry", "inspection_expiry", "last_service_date",
                  "next_service_due", "next_service_km", "notes"]

    def clean_registration_no(self):
        return self.cleaned_data["registration_no"].upper().strip()


class FuelLogForm(StyledModelForm):
    class Meta:
        model = FuelLog
        fields = ["date", "litres", "amount", "odometer_km", "station", "filled_by"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["filled_by"].queryset = Employee.objects.filter(is_driver=True)


class MaintenanceLogForm(StyledModelForm):
    full_width_fields = ("description",)

    class Meta:
        model = MaintenanceLog
        fields = ["date", "kind", "description", "garage", "cost", "odometer_km", "next_service_due"]
        widgets = {"date": __import__("django.forms", fromlist=["DateInput"]).DateInput(),
                   "next_service_due": __import__("django.forms", fromlist=["DateInput"]).DateInput()}
