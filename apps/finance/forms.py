from datetime import timedelta

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from apps.contacts.models import Client
from apps.core.forms import StyledForm, StyledModelForm
from apps.suppliers.models import Bill

from .models import Budget, Expense, FinanceEvent, Invoice, InvoiceLine, PayrollLine, Receipt, TaxSetting

DT = "%Y-%m-%dT%H:%M"
CURRENCIES = [("KES", "KES"), ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")]


class InvoiceForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Invoice
        fields = ["client", "issue_date", "due_date", "currency", "vat_rate", "prices_include_vat", "notes",
                  "recur_every_months"]
        widgets = {"issue_date": forms.DateInput(), "due_date": forms.DateInput(),
                   "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["client"].queryset = Client.objects.alive()
        self.fields["currency"] = forms.ChoiceField(choices=CURRENCIES, initial=self.instance.currency or "KES")
        self.fields["currency"].widget.attrs["class"] = "input select"
        self.fields["recur_every_months"].label = "Repeat every N months"

    def clean(self):
        d = super().clean()
        if d.get("issue_date") and d.get("due_date") and d["due_date"] < d["issue_date"]:
            self.add_error("due_date", "The due date can't be before the issue date.")
        return d


class InvoiceLineForm(StyledModelForm):
    class Meta:
        model = InvoiceLine
        fields = ["description", "quantity", "unit_price", "vat_applicable"]
        widgets = {"quantity": forms.NumberInput(attrs={"step": "0.5"}),
                   "unit_price": forms.NumberInput(attrs={"step": "0.01"})}


InvoiceLineFormSet = inlineformset_factory(Invoice, InvoiceLine, form=InvoiceLineForm, extra=0, can_delete=True,
                                           min_num=1, validate_min=True)


class ReceiptPaymentForm(StyledForm):
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2)
    method = forms.ChoiceField(choices=Receipt.METHODS)
    reference = forms.CharField(required=False, max_length=60, label="Transaction code")
    received_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT),
                                      input_formats=[DT], label="Received")
    note = forms.CharField(required=False, max_length=160)


class MatchForm(StyledForm):
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.none())

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["invoice"].queryset = Invoice.objects.filter(status__in=[Invoice.SENT, Invoice.PART])


class MpesaImportForm(StyledForm):
    file = forms.FileField(label="M-PESA statement (CSV)", help_text="Columns: date, amount, reference, account")


class ExpenseForm(StyledModelForm):
    full_width_fields = ("description",)

    class Meta:
        model = Expense
        fields = ["date", "category", "payee", "description", "amount", "vat_amount", "method", "receipt_file",
                  "departure", "vehicle", "recurring_monthly"]
        widgets = {"date": forms.DateInput()}

    def clean(self):
        d = super().clean()
        if d.get("amount") is not None and d.get("vat_amount") is not None and d["vat_amount"] > d["amount"]:
            self.add_error("vat_amount", "VAT can't exceed the amount.")
        return d


class BillPayForm(StyledForm):
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2, required=False,
                                help_text="Leave empty to pay the full balance")
    method = forms.ChoiceField(choices=[("bank", "Bank / RTGS"), ("mpesa", "M-PESA"), ("cheque", "Cheque"),
                                        ("cash", "Cash"), ("card", "Card")])
    reference = forms.CharField(required=False, max_length=60)


class PayrollLineForm(StyledForm):
    allowances = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2)
    other_deductions = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2, label="Advances / loans")


class TaxSettingForm(StyledModelForm):
    class Meta:
        model = TaxSetting
        fields = ["value", "note"]


class BudgetForm(StyledModelForm):
    class Meta:
        model = Budget
        fields = ["period_type", "year", "index", "category", "amount"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from .chart import CATEGORY_ACCOUNT

        self.fields["category"] = forms.ChoiceField(choices=[("revenue", "Revenue target")] + [
            (k, k.replace("_", " ").title()) for k in CATEGORY_ACCOUNT])
        self.fields["category"].widget.attrs["class"] = "input select"
        self.fields["year"].initial = timezone.localdate().year
        self.fields["index"].initial = timezone.localdate().month

    def validate_unique(self):
        pass


class EventForm(StyledModelForm):
    class Meta:
        model = FinanceEvent
        fields = ["title", "date", "category", "notes"]
        widgets = {"date": forms.DateInput()}


class PayrollRunForm(StyledForm):
    month = forms.DateField(widget=forms.DateInput, label="Pay month (any day)")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["month"].initial = timezone.localdate().replace(day=1)
