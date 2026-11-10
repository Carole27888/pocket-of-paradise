from django import forms
from django.utils import timezone

from apps.catalog.models import TourPackage
from apps.core.forms import StyledForm, StyledModelForm

from .models import Campaign, Channel, HashtagSet, MediaAsset, Post, TrackedLink

DT = "%Y-%m-%dT%H:%M"


class CampaignForm(StyledModelForm):
    full_width_fields = ("audience_notes", "creative_notes", "channels")

    class Meta:
        model = Campaign
        fields = ["name", "objective", "channels", "budget", "start_date", "end_date", "status", "package",
                  "audience_notes", "creative_notes"]
        widgets = {"start_date": forms.DateInput(), "end_date": forms.DateInput(),
                   "channels": forms.CheckboxSelectMultiple(), "creative_notes": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        d = super().clean()
        if d.get("start_date") and d.get("end_date") and d["end_date"] < d["start_date"]:
            self.add_error("end_date", "The campaign can't end before it starts.")
        if d.get("budget") is not None and d["budget"] < 0:
            self.add_error("budget", "Budget can't be negative.")
        return d


class PostForm(StyledModelForm):
    full_width_fields = ("caption", "channels")

    class Meta:
        model = Post
        fields = ["title", "caption", "channels", "format", "package", "media", "scheduled_at", "status"]
        widgets = {"channels": forms.CheckboxSelectMultiple(), "caption": forms.Textarea(attrs={"rows": 4}),
                   "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DT)}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["scheduled_at"].input_formats = [DT]
        self.fields["status"].choices = [c for c in Post.STATUSES if c[0] != Post.PUBLISHED] if not (
            self.instance.pk and self.instance.status == Post.PUBLISHED) else Post.STATUSES

    def clean(self):
        d = super().clean()
        if d.get("status") in (Post.SCHEDULED, Post.NEEDS) and not d.get("scheduled_at"):
            self.add_error("scheduled_at", "Choose when it should go out.")
        if not d.get("channels"):
            self.add_error("channels", "Pick at least one channel.")
        return d


class MediaForm(StyledModelForm):
    class Meta:
        model = MediaAsset
        fields = ["title", "kind", "file", "package", "tags"]


class HashtagForm(StyledModelForm):
    class Meta:
        model = HashtagSet
        fields = ["name", "tags"]


class LinkForm(StyledModelForm):
    class Meta:
        model = TrackedLink
        fields = ["name", "slug", "destination", "campaign", "channel", "medium", "content"]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["slug"].help_text = "Short name in the link: /l/<slug>/"
        self.fields["destination"].help_text = "Where visitors land, e.g. https://pocketofparadise.co.ke/mara"

    def clean_destination(self):
        d = self.cleaned_data["destination"].strip()
        if not d.startswith(("http://", "https://", "/")):
            raise forms.ValidationError("Use a full https:// link or a path starting with /.")
        return d


class ReplyForm(StyledForm):
    text = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), max_length=1000, label="Reply")


class ConvertForm(StyledForm):
    phone = forms.CharField(max_length=30, label="Guest phone")


class WorkflowForm(StyledForm):
    template = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Message text")


class LeadCaptureForm(StyledForm):
    full_name = forms.CharField(max_length=120, label="Your name")
    phone = forms.CharField(max_length=30, label="Phone / WhatsApp")
    email = forms.EmailField(required=False)
    package = forms.ModelChoiceField(queryset=TourPackage.objects.none(), required=False, label="Interested in")
    adults = forms.IntegerField(min_value=1, max_value=60, initial=2)
    travel_date = forms.DateField(required=False, label="Roughly when?")
    message = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Anything we should know?")
    event = forms.CharField(required=False, max_length=80, widget=forms.HiddenInput())
    website = forms.CharField(required=False, widget=forms.HiddenInput())       # honeypot: real people leave it empty

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["package"].queryset = TourPackage.objects.alive().filter(status__in=["published", "seasonal"])
        self.fields["website"].widget.attrs.pop("required", None)

    def clean_phone(self):
        from apps.core.phone import normalise_phone

        p = normalise_phone(self.cleaned_data["phone"])
        if len(p.lstrip("+")) < 9:
            raise forms.ValidationError("Enter a valid phone number.")
        return p
