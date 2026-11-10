from django import forms

from apps.core.forms import StyledModelForm

from .models import Employee


class EmployeeForm(StyledModelForm):
    full_width_fields = ("specialties",)

    class Meta:
        model = Employee
        fields = ["full_name", "title", "department", "phone", "email", "is_sales_agent", "is_guide", "is_driver",
                  "on_duty", "leave_from", "leave_to", "daily_enquiry_limit", "languages", "specialties",
                  "basic_salary", "payout_method", "bank_name", "bank_account", "mpesa_number", "kra_pin",
                  "nssf_no", "shif_no", "driving_license_no", "driving_license_expiry", "psv_badge_no",
                  "psv_badge_expiry", "guide_certification", "guide_certification_expiry", "is_active"]
        widgets = {"specialties": forms.CheckboxSelectMultiple}
