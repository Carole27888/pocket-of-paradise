from django import forms

from .models import Mailbox


class MailboxForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(render_value=False), required=False, help_text="Leave blank to keep the saved password.")

    class Meta:
        model = Mailbox
        fields = ["email", "display_name", "smtp_host", "smtp_port", "security", "username", "password", "bcc_self", "imap_host", "imap_port", "imap_security"]

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if not pw and self.instance.pk:
            return self.instance.password
        return pw
