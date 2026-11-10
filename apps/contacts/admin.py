from django.contrib import admin

from .models import Client, TravelPartyMember


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "nationality", "is_international", "created_at")
    list_filter = ("is_international",)
    search_fields = ("full_name", "phone", "email")
    exclude = ("passport_number",)  # encrypted; edit via the app


admin.site.register(TravelPartyMember)
