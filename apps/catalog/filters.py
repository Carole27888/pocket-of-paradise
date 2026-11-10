import django_filters as df

from .models import Accommodation, Destination, TourPackage


class DestinationFilter(df.FilterSet):
    region = df.ChoiceFilter(choices=Destination.REGION_CHOICES, empty_label="All regions")
    country = df.ChoiceFilter(choices=Destination._meta.get_field("country").choices, empty_label="All countries")

    class Meta:
        model = Destination
        fields = ["region", "country"]


class PackageFilter(df.FilterSet):
    package_type = df.ChoiceFilter(choices=TourPackage.TYPE_CHOICES, empty_label="All types")
    status = df.ChoiceFilter(choices=TourPackage.STATUS_CHOICES, empty_label="All statuses")
    destinations = df.ModelChoiceFilter(queryset=Destination.objects.all(), empty_label="All destinations")

    class Meta:
        model = TourPackage
        fields = ["package_type", "status", "destinations"]


class AccommodationFilter(df.FilterSet):
    accommodation_type = df.ChoiceFilter(choices=Accommodation.TYPE_CHOICES, empty_label="All types")
    destination = df.ModelChoiceFilter(queryset=Destination.objects.all(), empty_label="All destinations")

    class Meta:
        model = Accommodation
        fields = ["accommodation_type", "destination"]
