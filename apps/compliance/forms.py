from django import forms

from apps.contacts.models import Client
from apps.core.forms import StyledModelForm

from .models import DataBreach, DataRequest


class DataRequestForm(StyledModelForm):
    full_width_fields = ("details", "identity_note")

    class Meta:
        model = DataRequest
        fields = ["kind", "requester_name", "requester_email", "requester_phone", "client", "received_on", "due_on", "details"]
        widgets = {"received_on": forms.DateInput(), "due_on": forms.DateInput(), "details": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["client"].queryset = Client.objects.alive().order_by("full_name")
        self.fields["due_on"].required = False
        self.fields["due_on"].help_text = "Leave empty for 30 days from receipt"

    def clean(self):
        d = super().clean()
        if d.get("received_on") and d.get("due_on") and d["due_on"] < d["received_on"]:
            self.add_error("due_on", "The due date can't be before the request was received.")
        if not d.get("due_on") and d.get("received_on"):
            from datetime import timedelta

            d["due_on"] = d["received_on"] + timedelta(days=30)
        return d


class BreachForm(StyledModelForm):
    full_width_fields = ("what_happened", "actions_taken", "data_affected")

    class Meta:
        model = DataBreach
        fields = ["title", "what_happened", "data_affected", "people_affected", "severity", "detected_at", "contained_at",
                  "reported_at", "notified_at", "actions_taken"]
        widgets = {k: forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
                   for k in ("detected_at", "contained_at", "reported_at", "notified_at")}
        widgets |= {"what_happened": forms.Textarea(attrs={"rows": 3}), "actions_taken": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        for k in ("detected_at", "contained_at", "reported_at", "notified_at"):
            self.fields[k].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean(self):
        d = super().clean()
        det = d.get("detected_at")
        for k in ("contained_at", "reported_at", "notified_at"):
            if det and d.get(k) and d[k] < det:
                self.add_error(k, "This can't be before the breach was detected.")
        return d
