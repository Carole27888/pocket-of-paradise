from rest_framework import serializers

from apps.catalog.models import Destination, ItineraryDay, TourPackage
from apps.fleet.models import Vehicle
from apps.leads.models import Enquiry, Purpose, Source
from apps.people.models import Employee


class DestinationSerializer(serializers.ModelSerializer):
    region_label = serializers.CharField(source="get_region_display", read_only=True)

    class Meta:
        model = Destination
        fields = ["id", "slug", "name", "region", "region_label", "country", "summary", "best_time_to_visit",
                  "activities", "latitude", "longitude"]


class ItineraryDaySerializer(serializers.ModelSerializer):
    accommodation = serializers.StringRelatedField()
    meals = serializers.CharField(source="meals_code")

    class Meta:
        model = ItineraryDay
        fields = ["day_number", "title", "description", "accommodation", "meals", "activities", "drive_km",
                  "drive_hours"]


class PackageSerializer(serializers.ModelSerializer):
    destinations = serializers.SlugRelatedField(many=True, read_only=True, slug_field="slug")
    itinerary = serializers.SerializerMethodField()

    class Meta:
        model = TourPackage
        fields = ["id", "code", "slug", "title", "package_type", "status", "days", "nights", "currency",
                  "price_per_person", "single_supplement", "child_rate", "min_group", "max_group", "summary",
                  "destinations", "inclusion_list", "exclusion_list", "itinerary"]

    def get_itinerary(self, obj):
        if not obj.itinerary_id:
            return []
        return ItineraryDaySerializer(obj.itinerary.days.select_related("accommodation"), many=True).data


class EnquirySerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    agent = serializers.StringRelatedField(source="assigned_agent", read_only=True)

    class Meta:
        model = Enquiry
        fields = ["id", "reference", "client_name", "source", "purpose", "package", "destination",
                  "destination_text", "adults", "children", "travel_date_from", "stage", "quotation_status",
                  "agent", "created_at"]
        read_only_fields = fields


class EnquiryCreateSerializer(serializers.Serializer):
    """Used by the website form / future booking widget."""

    full_name = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=30)
    email = serializers.EmailField(required=False, allow_blank=True)
    nationality = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(default=Source.WEBSITE, max_length=30)
    purpose = serializers.ChoiceField(choices=Purpose.choices, default=Purpose.NEW)
    package = serializers.PrimaryKeyRelatedField(queryset=TourPackage.objects.alive(), required=False, allow_null=True)
    destination = serializers.PrimaryKeyRelatedField(queryset=Destination.objects.alive(), required=False,
                                                     allow_null=True)
    destination_text = serializers.CharField(required=False, allow_blank=True)
    adults = serializers.IntegerField(min_value=1, default=2)
    children = serializers.IntegerField(min_value=0, default=0)
    travel_date_from = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class VehicleSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    driver = serializers.StringRelatedField(source="current_driver")

    class Meta:
        model = Vehicle
        fields = ["id", "registration_no", "nickname", "vehicle_type", "seats", "status", "status_label", "driver"]


class GuideSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_on_duty_display", read_only=True)

    class Meta:
        model = Employee
        fields = ["id", "full_name", "is_guide", "is_driver", "on_duty", "status_label", "languages"]


class DepartureSerializer(serializers.ModelSerializer):
    package_title = serializers.CharField(source="package.title", read_only=True)
    stage_label = serializers.CharField(source="get_stage_display", read_only=True)
    guide = serializers.StringRelatedField()
    driver = serializers.StringRelatedField()
    vehicle = serializers.StringRelatedField()
    seats_sold = serializers.IntegerField(read_only=True)
    missing_resources = serializers.ListField(read_only=True)

    class Meta:
        from apps.trips.models import Departure as _D

        model = _D
        fields = ["id", "reference", "package", "package_title", "start_date", "end_date", "capacity", "seats_sold", "stage",
                  "stage_label", "guide", "driver", "vehicle", "meeting_point", "pickup_time", "missing_resources"]


class ManifestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    is_child = serializers.BooleanField()
    age = serializers.IntegerField(allow_null=True)
    nationality = serializers.CharField()
    pickup_point = serializers.CharField()
    dietary_notes = serializers.CharField()
    checked_in_at = serializers.DateTimeField(allow_null=True)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if self.context.get("pii"):
            data.update(passport=instance.masked_passport, medical_notes=instance.medical_notes,
                        emergency_contact=instance.emergency_contact, phone=instance.phone)
        return data


class BookingSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.full_name", read_only=True)
    agent_name = serializers.CharField(source="agent.full_name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    amount_paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        from apps.sales.models import Booking as _B

        model = _B
        fields = ["id", "reference", "client_name", "agent_name", "title", "departure", "travel_start", "travel_end", "adults",
                  "children", "currency", "total_amount", "amount_paid", "balance", "status", "status_label"]


class GuestSerializer(serializers.ModelSerializer):
    class Meta:
        from apps.contacts.models import Client as _C

        model = _C
        fields = ["id", "full_name", "phone", "email", "nationality", "is_international", "preferred_language"]
