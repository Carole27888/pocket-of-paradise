from datetime import timedelta

import django_filters as df
from django import forms
from django.db.models import F, Q
from django.utils import timezone

from apps.fleet.models import Vehicle
from apps.leads.models import Enquiry
from apps.people.models import Employee

from .models import TripMovement
from .services import QUEUE_STATES, filter_state

PERIODS = [("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days"), ("all", "Any date")]


class QueueFilter(df.FilterSet):
    state = df.ChoiceFilter(choices=QUEUE_STATES, method="filter_state", empty_label="Every status", label="Status")
    period = df.ChoiceFilter(choices=PERIODS, method="filter_period", empty_label=None, label="Captured")
    assigned_agent = df.ModelChoiceFilter(queryset=Employee.objects.filter(is_sales_agent=True), empty_label="All agents")

    class Meta:
        model = Enquiry
        fields = ["state", "period", "assigned_agent"]

    def __init__(self, data=None, *a, **kw):
        data = data.copy() if data is not None else {}
        data.setdefault("period", "all")
        super().__init__(data, *a, **kw)

    def filter_state(self, qs, name, value):
        return filter_state(qs, value)

    def filter_period(self, qs, name, value):
        today = timezone.localdate()
        days = {"today": 0, "7d": 6, "30d": 29}.get(value)
        return qs if days is None else qs.filter(created_at__date__gte=today - timedelta(days=days))


class MovementFilter(df.FilterSet):
    status = df.ChoiceFilter(choices=[("open", "Still out"), ("late", "Late (still out)"), ("back", "Checked in"),
                                      ("back_late", "Came back late")],
                             method="filter_status", empty_label="Out & back", label="Status")
    vehicle = df.ModelChoiceFilter(queryset=Vehicle.objects.all(), empty_label="All vehicles")
    crew = df.ModelChoiceFilter(queryset=Employee.objects.filter(Q(is_driver=True) | Q(is_guide=True)),
                                method="filter_crew", empty_label="All drivers & guides", label="Driver / guide")
    purpose = df.ChoiceFilter(choices=TripMovement.PURPOSES, empty_label="All purposes")
    out_after = df.DateFilter(field_name="checked_out_at", lookup_expr="date__gte", label="From",
                              widget=forms.DateInput(attrs={"type": "date"}))
    out_before = df.DateFilter(field_name="checked_out_at", lookup_expr="date__lte", label="To",
                               widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = TripMovement
        fields = ["status", "vehicle", "purpose"]

    def filter_status(self, qs, name, value):
        now = timezone.now()
        return {
            "open": qs.filter(checked_in_at__isnull=True),
            "late": qs.filter(checked_in_at__isnull=True, expected_return_at__lt=now),
            "back": qs.filter(checked_in_at__isnull=False),
            "back_late": qs.filter(checked_in_at__gt=F("expected_return_at")),
        }.get(value, qs)

    def filter_crew(self, qs, name, value):
        return qs.filter(Q(driver=value) | Q(guide=value))
