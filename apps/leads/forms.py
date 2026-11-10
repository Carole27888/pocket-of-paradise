from django import forms

from apps.catalog.models import Destination, TourPackage
from apps.core.forms import StyledForm, StyledModelForm
from apps.people.models import Employee

from .models import Enquiry, Purpose, Source, Stage, source_choices


class EnquiryCreateForm(StyledForm):
    full_width_fields = ("notes",)
    full_name = forms.CharField(label="Client name", max_length=120)
    phone = forms.CharField(label="Phone number", max_length=30)
    email = forms.EmailField(required=False)
    nationality = forms.CharField(required=False, initial="Kenyan")
    source = forms.ChoiceField(choices=source_choices)
    purpose = forms.ChoiceField(choices=Purpose.choices)
    package = forms.ModelChoiceField(queryset=TourPackage.objects.none(), required=False, label="Package of interest")
    destination = forms.ModelChoiceField(queryset=Destination.objects.none(), required=False)
    travel_date_from = forms.DateField(required=False, label="Preferred travel date", widget=forms.DateInput)
    adults = forms.IntegerField(min_value=1, initial=2)
    children = forms.IntegerField(min_value=0, initial=0)
    budget_text = forms.CharField(required=False, label="Budget", help_text="e.g. KES 60K–80K pp or $2,500 pp")
    assigned_agent = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False, label="Assign to")
    notes = forms.CharField(required=False, widget=forms.Textarea)
    brought_by = forms.ModelChoiceField(queryset=Employee.objects.none(), required=False, label="Who's that (brought the lead)",
                                        help_text="The staff member who met or brought this lead.")
    remark = forms.CharField(required=False, max_length=200, help_text="One line shown in the leads list.")

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["brought_by"].queryset = Employee.objects.filter(is_active=True)
        self.fields["package"].queryset = TourPackage.objects.alive().exclude(status="retired")
        self.fields["destination"].queryset = Destination.objects.alive()
        self.fields["assigned_agent"].queryset = Employee.objects.filter(is_sales_agent=True, is_active=True)

    def clean_assigned_agent(self):
        agent = self.cleaned_data.get("assigned_agent")
        if agent and not agent.is_available:
            raise forms.ValidationError(f"{agent} is {agent.get_on_duty_display().lower()} and cannot take enquiries.")
        return agent


class EnquiryEditForm(StyledModelForm):
    full_width_fields = ("notes",)

    class Meta:
        model = Enquiry
        fields = ["package", "destination", "destination_text", "purpose", "adults", "children", "dates_flexible", "brought_by", "remark",
                  "travel_date_from", "travel_date_to", "budget_currency", "budget_min_pp", "budget_max_pp",
                  "budget_text", "next_action", "notes"]


class StageForm(StyledForm):
    stage = forms.ChoiceField(choices=Stage.choices)
    lost_reason = forms.CharField(required=False, max_length=160)

    def clean(self):
        d = super().clean()
        if d.get("stage") == Stage.LOST and not d.get("lost_reason"):
            self.add_error("lost_reason", "Tell us why the enquiry was lost.")
        return d


class AssignForm(StyledForm):
    agent = forms.ModelChoiceField(queryset=Employee.objects.none())

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["agent"].queryset = Employee.objects.filter(is_sales_agent=True, is_active=True)


class NoteForm(StyledForm):
    activity_type = forms.ChoiceField(choices=[("note", "Note"), ("call", "Call"), ("whatsapp", "WhatsApp"),
                                               ("sms", "SMS"), ("email", "Email")], initial="note")
    details = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
