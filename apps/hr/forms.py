from django import forms

from .models import LeaveType, PublicHoliday


class LeaveRequestForm(forms.Form):
    leave_type = forms.ModelChoiceField(LeaveType.objects.filter(active=True), empty_label=None)
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    half_day = forms.ChoiceField(choices=[("", "Full day(s)"), ("am", "Morning only"), ("pm", "Afternoon only")], required=False)
    reason = forms.CharField(max_length=240, required=False)
    document = forms.FileField(required=False)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(active=True)
        for f in self.fields.values():
            f.widget.attrs.setdefault("class", "input")


class HolidayForm(forms.ModelForm):
    class Meta:
        model = PublicHoliday
        fields = ["date", "name"]
        widgets = {"date": forms.DateInput(attrs={"type": "date", "class": "input"}), "name": forms.TextInput(attrs={"class": "input"})}
