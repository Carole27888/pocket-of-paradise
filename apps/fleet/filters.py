import django_filters as df

from .models import Vehicle


class VehicleFilter(df.FilterSet):
    status = df.ChoiceFilter(choices=Vehicle.STATUS_CHOICES, empty_label="All statuses")
    vehicle_type = df.ChoiceFilter(choices=Vehicle.TYPE_CHOICES, empty_label="All types")

    class Meta:
        model = Vehicle
        fields = ["status", "vehicle_type"]
