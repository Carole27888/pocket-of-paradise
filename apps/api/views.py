from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.catalog.models import Destination, TourPackage
from apps.fleet.models import Vehicle
from apps.leads import services
from apps.leads.views import visible_enquiries
from apps.people.models import Employee

from . import serializers as s
from .permissions import ModuleAccess


from rest_framework.views import APIView  # noqa: E402


class MeView(APIView):
    """The signed-in user: roles, accessible modules and permissions the mobile app can use to shape its UI."""

    def get(self, request):
        from apps.core.modules import modules_for

        u = request.user
        return Response({"id": u.pk, "email": u.email, "full_name": u.full_name, "roles": sorted(u.role_names),
                         "primary_role": u.primary_role, "read_only": u.is_read_only,
                         "modules": [m.key for m in modules_for(u)],
                         "employee_id": getattr(getattr(u, "employee", None), "pk", None)})


class DestinationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.DestinationSerializer
    lookup_field = "slug"
    filterset_fields = ["region", "country"]

    def get_queryset(self):
        return Destination.objects.alive()


class PackageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = s.PackageSerializer
    lookup_field = "slug"
    filterset_fields = ["package_type", "status"]

    def get_queryset(self):
        return TourPackage.objects.alive().prefetch_related("destinations")

    @action(detail=True)
    def availability(self, request, slug=None):
        """Seats/vehicles available (bookings arrive in Phase 3; fleet capacity is live now)."""
        pkg = self.get_object()
        vehicles = Vehicle.objects.filter(status=Vehicle.AVAILABLE)
        return Response({"package": pkg.code, "vehicles_available": vehicles.count(),
                         "seats_available": sum(v.seats for v in vehicles), "max_group": pkg.max_group})


class EnquiryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     viewsets.GenericViewSet):
    filterset_fields = ["stage", "source", "quotation_status"]
    permission_classes = [ModuleAccess]
    required_modules = ("operations", "sales", "marketing", "frontdesk")

    def get_queryset(self):
        return visible_enquiries(self.request.user)

    def get_serializer_class(self):
        return s.EnquiryCreateSerializer if self.action == "create" else s.EnquirySerializer

    def create(self, request, *args, **kwargs):
        ser = s.EnquiryCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        enq = services.create_enquiry(
            client_data={"full_name": d["full_name"], "phone": d["phone"], "email": d.get("email", ""),
                         "nationality": d.get("nationality", "")},
            enquiry_data={"source": d["source"], "purpose": d["purpose"], "package": d.get("package"),
                          "destination": d.get("destination"), "destination_text": d.get("destination_text", ""),
                          "adults": d["adults"], "children": d["children"],
                          "travel_date_from": d.get("travel_date_from"), "notes": d.get("notes", ""),
                          "captured_via": "api"},
            user=request.user,
        )
        return Response(s.EnquirySerializer(enq).data, status=status.HTTP_201_CREATED)


class MovementViewSet(viewsets.ViewSet):
    """Vehicle and guide/driver positions."""

    permission_classes = [ModuleAccess]
    required_modules = ("operations", "frontdesk")

    def list(self, request):
        vehicles = Vehicle.objects.select_related("current_driver")
        guides = Employee.objects.filter(Q(is_guide=True) | Q(is_driver=True), is_active=True)
        return Response({"vehicles": s.VehicleSerializer(vehicles, many=True).data,
                         "guides": s.GuideSerializer(guides, many=True).data})


class DepartureViewSet(viewsets.ReadOnlyModelViewSet):
    """Departures the user may see (guides: only their own), with manifest and check-in."""

    permission_classes = [ModuleAccess]
    required_modules = ("trips",)
    filterset_fields = ["stage", "package"]

    def get_serializer_class(self):
        return s.DepartureSerializer

    def get_queryset(self):
        from apps.trips.views import visible_departures

        return visible_departures(self.request.user).prefetch_related("manifest")

    @action(detail=True, methods=["get"])
    def manifest(self, request, pk=None):
        dep = self.get_object()
        pii = request.user.can_manage_trips or request.user.has_role("Guide / Driver")
        return Response(s.ManifestSerializer(dep.manifest.all(), many=True, context={"pii": pii}).data)

    @action(detail=True, methods=["post"], url_path=r"manifest/(?P<entry_id>\d+)/check-in")
    def check_in(self, request, pk=None, entry_id=None):
        from apps.trips import services as trip_services

        dep = self.get_object()
        if not (request.user.can_manage_trips or request.user.has_role("Guide / Driver")):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        entry = dep.manifest.filter(pk=entry_id).first()
        if entry is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        trip_services.check_in(entry, request.user, undo=bool(request.data.get("undo")))
        return Response(s.ManifestSerializer(entry, context={"pii": True}).data)


class BookingViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [ModuleAccess]
    required_modules = ("sales", "finance")
    filterset_fields = ["status", "agent"]

    def get_serializer_class(self):
        return s.BookingSerializer

    def get_queryset(self):
        from apps.sales.services import bookings_for

        return bookings_for(self.request.user)


class GuestViewSet(viewsets.ReadOnlyModelViewSet):
    """Guests (clients) the user may see. Passport numbers are never exposed."""

    permission_classes = [ModuleAccess]
    required_modules = ("operations", "sales", "marketing", "frontdesk")
    search_fields = ["full_name", "phone", "email"]

    def get_serializer_class(self):
        return s.GuestSerializer

    def get_queryset(self):
        from apps.contacts.views import visible_clients

        return visible_clients(self.request.user)
