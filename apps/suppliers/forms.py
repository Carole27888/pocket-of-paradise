from datetime import timedelta

from django import forms
from django.utils import timezone

from apps.core.forms import StyledModelForm

from .models import Bill, Supplier


class SupplierForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Supplier
        fields = ["name", "kind", "contact_name", "phone", "email", "location", "kra_pin", "payment_terms_days",
                  "bank_details", "withholding_applies", "is_active", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class BillForm(StyledModelForm):
    full_width_fields = ("description",)

    class Meta:
        model = Bill
        fields = ["supplier", "departure", "supplier_ref", "category", "description", "bill_date", "due_date",
                  "currency", "amount", "vat_amount", "prepayment"]
        widgets = {"bill_date": forms.DateInput(), "due_date": forms.DateInput()}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["supplier"].queryset = Supplier.objects.alive().filter(is_active=True)
        self.fields["due_date"].required = False
        self.fields["due_date"].help_text = "Leave empty to use the supplier's payment terms."
        self.fields["currency"] = forms.ChoiceField(choices=[("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")])
        self.fields["currency"].widget.attrs["class"] = "input select"

    def clean(self):
        d = super().clean()
        if d.get("amount") is not None and d.get("vat_amount") is not None and d["vat_amount"] > d["amount"]:
            self.add_error("vat_amount", "VAT can't exceed the amount.")
        if d.get("amount") is not None and d["amount"] <= 0:
            self.add_error("amount", "Enter an amount above zero.")
        if not d.get("due_date") and d.get("supplier") and d.get("bill_date"):
            d["due_date"] = d["bill_date"] + timedelta(days=d["supplier"].payment_terms_days)
        if not d.get("due_date"):
            self.add_error("due_date", "Set a due date.")
        return d

    def save(self, commit=True):
        from apps.sales.services import fx_rate

        bill = super().save(commit=False)
        bill.fx_rate = fx_rate(bill.currency)
        if commit:
            bill.save()
        return bill
