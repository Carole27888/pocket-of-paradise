from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Enquiry, LeadActivity, LeadAssignment


class AssignmentInline(admin.TabularInline):
    model = LeadAssignment
    extra = 0


class ActivityInline(admin.TabularInline):
    model = LeadActivity
    extra = 0


@admin.register(Enquiry)
class EnquiryAdmin(SimpleHistoryAdmin):
    list_display = ("reference", "client", "source", "stage", "quotation_status", "assigned_agent", "created_at")
    list_filter = ("stage", "source", "quotation_status", "purpose", "assigned_agent")
    search_fields = ("reference", "client__full_name", "client__phone")
    raw_id_fields = ("client",)
    inlines = [AssignmentInline, ActivityInline]
