from django.contrib import admin

from .models import FuelLog, Vehicle


class FuelInline(admin.TabularInline):
    model = FuelLog
    extra = 0


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration_no", "nickname", "vehicle_type", "seats", "status", "current_driver",
                    "insurance_expiry", "inspection_expiry", "next_service_due")
    list_filter = ("vehicle_type", "status")
    search_fields = ("registration_no", "nickname")
    inlines = [FuelInline]
