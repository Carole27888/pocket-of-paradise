from django.contrib import admin

from .jobs.models import Job, JobLock, ScheduleState
from .models import CompanySetting, Currency, ExchangeRate, Sequence


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "symbol", "is_base", "is_active")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "date", "rate_to_base", "source")
    list_filter = ("currency",)
    date_hierarchy = "date"


@admin.register(Sequence)
class SequenceAdmin(admin.ModelAdmin):
    list_display = ("name", "last_value")


@admin.register(CompanySetting)
class CompanySettingAdmin(admin.ModelAdmin):
    list_display = ("name", "kra_pin", "paybill")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("pk", "task_name", "status", "attempts", "run_at", "finished_at")
    list_filter = ("status", "task_name")
    search_fields = ("task_name", "last_error")


admin.site.register(ScheduleState)
admin.site.register(JobLock)
admin.site.site_header = "Pocket of Paradise Travel Company · Admin"
admin.site.site_title = "Pocket of Paradise admin"
