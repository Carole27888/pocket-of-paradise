from django.contrib import admin

from .models import EmailTemplate, MailSettings, SentEmail

admin.site.register(EmailTemplate)
admin.site.register(MailSettings)
admin.site.register(SentEmail)
