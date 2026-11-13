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


class StarRatingInput(forms.Widget):
    """Clickable stars (1 to `stars`) with a "No rating" choice. Radio buttons underneath, so it works by keyboard and without JavaScript."""

    def __init__(self, attrs=None, stars=5):
        super().__init__(attrs)
        self.stars = stars

    def value_from_datadict(self, data, files, name):
        return data.get(name) or None

    def render(self, name, value, attrs=None, renderer=None):
        from django.utils.html import format_html, format_html_join

        base = (attrs or {}).get("id") or f"id_{name}"
        try:
            current = int(value) if value not in (None, "") else 0
        except (TypeError, ValueError):
            current = 0
        stars = format_html_join("", '<input type="radio" id="{0}_{1}" name="{2}" value="{1}"{3}><label for="{0}_{1}" title="{1} star{4}">'
                                     '<span aria-hidden="true">★</span><span class="visually-hidden">{1} star{4}</span></label>',
                                 ((base, n, name, " checked" if n == current else "", "" if n == 1 else "s") for n in range(self.stars, 0, -1)))
        return format_html('<div class="star-box"><div class="star-input" role="radiogroup" aria-label="Star rating">{}</div>'
                           '<label class="star-clear"><input type="radio" name="{}" value=""{}> No rating</label></div>',
                           stars, name, " checked" if not current else "")
