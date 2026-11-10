from django.contrib import admin

from .models import TripMovement


@admin.register(TripMovement)
class TripMovementAdmin(admin.ModelAdmin):
    list_display = ("reference", "vehicle", "driver", "guide", "purpose", "checked_out_at", "expected_return_at",
                    "checked_in_at", "odometer_out", "odometer_in")
    list_filter = ("purpose", "condition_in", "vehicle")
    search_fields = ("reference", "vehicle__registration_no", "driver__full_name", "guide__full_name")
    raw_id_fields = ("enquiry",)
    readonly_fields = ("reference", "late_alerted_at")

from .models import Appointment, EquipmentRecord, ShiftNote, Visitor  # noqa: E402

admin.site.register([Visitor, Appointment, EquipmentRecord, ShiftNote])
