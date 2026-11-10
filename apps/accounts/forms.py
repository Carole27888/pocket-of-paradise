from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group

from apps.core.forms import StyledForm, StyledModelForm
from apps.people.models import Employee

from .models import User


class ProfileForm(StyledModelForm):
    """My Profile: a person's own contact and payroll details (the Employee record)."""
    class Meta:
        model = Employee
        fields = ["full_name", "phone", "email", "payout_method", "kra_pin", "bank_name", "bank_account", "mpesa_number", "nssf_no", "shif_no"]
        labels = {"payout_method": "Pay method"}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        # the e-mail is how the person is known to the system: it can only be changed by an administrator
        self.fields["email"].disabled = True
        self.fields["email"].help_text = "Contact an administrator to change your e-mail address."

    # what Finance must be told about when it changes
    PAYROLL = ("payout_method", "kra_pin", "bank_name", "bank_account", "mpesa_number")


class AlertsForm(StyledModelForm):
    """My Profile → Alerts: where this person's notifications go."""
    class Meta:
        model = User
        fields = ["whatsapp_number", "notify_via"]

    def clean_whatsapp_number(self):
        from apps.core.phone import normalise_phone

        raw = (self.cleaned_data.get("whatsapp_number") or "").strip()
        if raw and not normalise_phone(raw):
            raise forms.ValidationError("That doesn't look like a phone number. Use e.g. 0712 345 678 or +254 712 345 678.")
        return normalise_phone(raw) if raw else ""


class LoginForm(StyledForm):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember = forms.BooleanField(required=False, initial=True, label="Keep me signed in on this device")

    def __init__(self, request=None, *a, **kw):
        self.request = request
        self.user = None
        super().__init__(*a, **kw)

    def clean(self):
        d = super().clean()
        if d.get("email") and d.get("password"):
            self.user = authenticate(self.request, email=d["email"].lower(), password=d["password"])
            if self.user is None or not self.user.is_active:
                raise forms.ValidationError("That email and password don't match an active account.")
        return d


class UserForm(StyledModelForm):
    roles = forms.ModelMultipleChoiceField(queryset=Group.objects.all().order_by("name"), required=False,
                                           widget=forms.CheckboxSelectMultiple)
    employee = forms.ModelChoiceField(queryset=Employee.objects.filter(is_active=True), required=False,
                                      help_text="Link to a staff profile (enables agent/guide features).")
    password = forms.CharField(required=False, widget=forms.PasswordInput, help_text="Leave blank to keep the current password.")
    full_width_fields = ("roles",)

    class Meta:
        model = User
        fields = ["full_name", "email", "whatsapp_number", "notify_via", "is_active", "is_staff"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        if self.instance.pk:
            self.fields["roles"].initial = self.instance.groups.all()
            self.fields["employee"].initial = getattr(self.instance, "employee", None)
        else:
            self.fields["password"].required = True
            self.fields["password"].help_text = "At least 8 characters."

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        user.email = user.email.lower()
        if user.whatsapp_number:
            from apps.core.phone import normalise_phone

            user.whatsapp_number = normalise_phone(user.whatsapp_number) or user.whatsapp_number
        user.save()
        user.groups.set(self.cleaned_data["roles"])
        emp = self.cleaned_data.get("employee")
        Employee.objects.filter(user=user).exclude(pk=getattr(emp, "pk", None)).update(user=None)
        if emp:
            emp.user = user
            emp.save(update_fields=["user"])
        return user
