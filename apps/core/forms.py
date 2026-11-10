from django import forms


class StyledFormMixin:
    """Adds design-system classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs.setdefault("class", "check")
            elif isinstance(w, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                w.attrs.setdefault("class", "choice-list")
            elif isinstance(w, forms.Select):
                w.attrs.setdefault("class", "input select")
            elif isinstance(w, forms.Textarea):
                w.attrs.setdefault("class", "input textarea")
                w.attrs.setdefault("rows", 3)
            else:
                w.attrs.setdefault("class", "input")
            if isinstance(w, forms.DateInput):
                w.input_type = "date"
                w.format = "%Y-%m-%d"
            if field.required:
                w.attrs.setdefault("required", True)


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    full_width_fields: tuple = ()

    def is_full(self, name):
        return name in self.full_width_fields


class StyledForm(StyledFormMixin, forms.Form):
    full_width_fields: tuple = ()


class CompanyForm(StyledModelForm):
    full_width_fields = ("address", "bank_details", "payment_note", "documents_list", "packing_list")

    class Meta:
        from .models import CompanySetting

        model = CompanySetting
        fields = ["name", "tagline", "kra_pin", "tra_license_no", "paybill", "phone", "email", "website", "address", "bank_details", "payment_note", "documents_list", "packing_list"]
        widgets = {"bank_details": forms.Textarea(attrs={"rows": 5}), "documents_list": forms.Textarea(attrs={"rows": 4}), "packing_list": forms.Textarea(attrs={"rows": 4})}
