from django.contrib import admin

from .models import (AssignmentRule, Booking, BookingPayment, CallLog, FollowUp, Quotation, QuotationItem,
                     SalesSettings, SalesTarget)


class ItemInline(admin.TabularInline):
    model = QuotationItem
    extra = 0


class PaymentInline(admin.TabularInline):
    model = BookingPayment
    extra = 0


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ("reference", "version", "enquiry", "status", "currency", "total", "sent_at")
    list_filter = ("status", "currency")
    search_fields = ("reference", "enquiry__reference", "enquiry__client__full_name")
    inlines = [ItemInline]


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("reference", "client", "status", "currency", "total_amount", "agent", "travel_start")
    list_filter = ("status", "currency", "agent")
    search_fields = ("reference", "client__full_name")
    inlines = [PaymentInline]


admin.site.register([AssignmentRule, CallLog, FollowUp, SalesSettings, SalesTarget])
