from django.contrib import admin

from .models import Accommodation, Destination, DestinationPhoto, Itinerary, ItineraryDay, RoomRate, TourPackage


class PhotoInline(admin.TabularInline):
    model = DestinationPhoto
    extra = 0


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "country", "is_featured", "deleted_at")
    list_filter = ("region", "country", "is_featured")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    inlines = [PhotoInline]


class RateInline(admin.TabularInline):
    model = RoomRate
    extra = 0


@admin.register(Accommodation)
class AccommodationAdmin(admin.ModelAdmin):
    list_display = ("name", "destination", "accommodation_type", "star_rating")
    list_filter = ("accommodation_type", "destination")
    search_fields = ("name",)
    inlines = [RateInline]


class DayInline(admin.StackedInline):
    model = ItineraryDay
    extra = 0


@admin.register(Itinerary)
class ItineraryAdmin(admin.ModelAdmin):
    list_display = ("title", "created_at")
    inlines = [DayInline]


@admin.register(TourPackage)
class TourPackageAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "package_type", "days", "price_per_person", "currency", "status")
    list_filter = ("package_type", "status", "destinations")
    search_fields = ("code", "title")
    filter_horizontal = ("destinations",)
