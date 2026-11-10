from django import forms

from apps.people.models import Employee

from .models import Task

DT = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "owner", "due_at", "priority", "details"]
        widgets = {"due_at": DT, "details": forms.Textarea(attrs={"rows": 3})}
        labels = {"owner": "Who does it", "due_at": "Due"}

    def __init__(self, *args, user=None, any_owner=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        qs = Employee.objects.filter(is_active=True, user__isnull=False).order_by("full_name")
        own = getattr(user, "employee", None)
        if not any_owner:
            qs = qs.filter(pk=own.pk) if own else qs.none()
        self.fields["owner"].queryset = qs
