from django import forms

from apps.core.forms import StyledModelForm

from .models import Client


class ClientForm(StyledModelForm):
    full_width_fields = ("notes",)
    passport_number = forms.CharField(required=False, help_text="Stored encrypted.")
    owner = forms.ModelChoiceField(
        queryset=None, required=False, label="Account owner (agent)",
        help_text="Leave empty on a new client to give them to the next agent in the round-robin rotation.")

    class Meta:
        model = Client
        fields = ["full_name", "phone", "alt_phone", "email", "nationality", "country_of_residence",
                  "passport_number", "preferred_language", "dietary_notes", "medical_notes", "notes", "owner"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from apps.people.models import Employee

        self.fields["owner"].queryset = Employee.objects.filter(is_sales_agent=True, is_active=True)
        self.fields["owner"].widget.attrs["class"] = "input select"
        self.fields["owner"].empty_label = "Next in rotation (round-robin)" if not self.instance.pk else "No owner"

    def save(self, commit=True):
        from django.utils import timezone

        from apps.sales.services import pick_owner

        client = super().save(commit=False)
        creating = client.pk is None
        if creating and not client.owner_id:
            client.owner = pick_owner()
        self.owner_changed = bool(client.owner_id) and ("owner" in self.changed_data or creating)
        if self.owner_changed or "owner" in self.changed_data:
            client.owner_assigned_at = timezone.now() if client.owner_id else None
        if commit:
            client.save()
        return client

    def clean_phone(self):
        from apps.core.phone import normalise_phone

        p = normalise_phone(self.cleaned_data.get("phone"))
        if p and Client.objects.alive().filter(phone=p).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another client already uses this phone number.")
        return p
