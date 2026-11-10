from django import forms
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe


class DropzoneInput(forms.ClearableFileInput):
    """A drag-and-drop picture box with preview and remove. Falls back to the plain file input without JavaScript."""

    def __init__(self, attrs=None, label="Drag a picture here or click to choose"):
        super().__init__(attrs)
        self.label = label

    def render(self, name, value, attrs=None, renderer=None):
        ctx = self.get_context(name, value, attrs)["widget"]
        ctx["dz_label"] = self.label
        ctx["current_url"] = value.url if value and hasattr(value, "url") and getattr(value, "name", "") else ""
        return mark_safe(render_to_string("components/dropzone.html", {"w": ctx}))  # noqa: S308 - template-escaped
