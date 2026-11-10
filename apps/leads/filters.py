import django_filters as df
from django import forms

from apps.people.models import Employee

from .models import Enquiry, Purpose, QuotationStatus, Source, Stage, source_choices


class EnquiryFilter(df.FilterSet):
    stage = df.ChoiceFilter(choices=Stage.choices, empty_label="All stages")
    source = df.ChoiceFilter(choices=source_choices, empty_label="All sources")
    purpose = df.ChoiceFilter(choices=Purpose.choices, empty_label="All purposes")
    quotation_status = df.ChoiceFilter(choices=QuotationStatus.choices, empty_label="Quoted & not quoted")
    assigned_agent = df.ModelChoiceFilter(queryset=Employee.objects.filter(is_sales_agent=True), empty_label="All agents")
    unassigned = df.ChoiceFilter(choices=[("yes", "Unassigned only")], method="filter_unassigned",
                                 empty_label="Assigned & unassigned", label="Assignment")
    created_after = df.DateFilter(field_name="created_at", lookup_expr="date__gte", label="From",
                                  widget=forms.DateInput(attrs={"type": "date"}))
    created_before = df.DateFilter(field_name="created_at", lookup_expr="date__lte", label="To",
                                   widget=forms.DateInput(attrs={"type": "date"}))

    def filter_unassigned(self, qs, name, value):
        return qs.filter(assigned_agent__isnull=True) if value == "yes" else qs

    class Meta:
        model = Enquiry
        fields = ["stage", "source", "purpose", "quotation_status", "assigned_agent"]
