from django.contrib import admin

from .models import IntegrationLog


@admin.register(IntegrationLog)
class IntegrationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "service", "action", "mode", "ok")
    list_filter = ("service", "mode", "ok")
