import django_filters as df
from django import forms

from apps.people.models import Employee

from .models import Booking, BookingStatus, CallLog, FollowUp, Quotation, QuotationStatus

def AGENTS(request=None):
    return Employee.objects.filter(is_sales_agent=True)

DATE = lambda: forms.DateInput(attrs={"type": "date"})  # noqa: E731


class QuotationFilter(df.FilterSet):
    status = df.ChoiceFilter(choices=QuotationStatus.choices, empty_label="All statuses")
    agent = df.ModelChoiceFilter(queryset=AGENTS, empty_label="All agents", field_name="enquiry__assigned_agent")
    currency = df.ChoiceFilter(choices=[("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")],
                               empty_label="All currencies")

    class Meta:
        model = Quotation
        fields = ["status", "agent", "currency"]


class BookingFilter(df.FilterSet):
    status = df.ChoiceFilter(choices=BookingStatus.choices, empty_label="All statuses")
    agent = df.ModelChoiceFilter(queryset=AGENTS, empty_label="All agents")
    travel_from = df.DateFilter(field_name="travel_start", lookup_expr="gte", label="Travel from", widget=DATE())
    travel_to = df.DateFilter(field_name="travel_start", lookup_expr="lte", label="Travel to", widget=DATE())

    class Meta:
        model = Booking
        fields = ["status", "agent"]


class CallFilter(df.FilterSet):
    outcome = df.ChoiceFilter(choices=CallLog.OUTCOMES, empty_label="All outcomes")
    direction = df.ChoiceFilter(choices=CallLog.DIRECTIONS, empty_label="In & out")
    agent = df.ModelChoiceFilter(queryset=AGENTS, empty_label="All agents")
    after = df.DateFilter(field_name="called_at", lookup_expr="date__gte", label="From", widget=DATE())
    before = df.DateFilter(field_name="called_at", lookup_expr="date__lte", label="To", widget=DATE())

    class Meta:
        model = CallLog
        fields = ["outcome", "direction", "agent"]


class FollowUpFilter(df.FilterSet):
    agent = df.ModelChoiceFilter(queryset=AGENTS, empty_label="All agents")
    kind = df.ChoiceFilter(choices=FollowUp.KINDS, empty_label="All types")

    class Meta:
        model = FollowUp
        fields = ["agent", "kind"]
