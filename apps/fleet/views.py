from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import DetailView

from apps.accounts.permissions import ModuleMixin, module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.lists import ListScreen

from .filters import VehicleFilter
from .forms import FuelLogForm, MaintenanceLogForm, VehicleForm
from .models import Vehicle


class VehicleList(ListScreen):
    model = Vehicle
    filterset_class = VehicleFilter
    search_fields = ["registration_no", "nickname", "make_model", "current_driver__full_name"]
    sort_fields = ["registration_no", "vehicle_type", "seats", "status", "insurance_expiry", "inspection_expiry",
                   "next_service_due"]
    default_sort = "registration_no"
    template_name = "fleet/vehicle_list.html"
    partial_template = "fleet/partials/vehicle_table.html"
    title = "Fleet"
    subtitle = "Safari vans and Land Cruisers: status, drivers, insurance, inspection and service dates."
    export_name = "fleet"
    export_columns = [("Registration", "registration_no"), ("Nickname", "nickname"), ("Type", "get_vehicle_type_display"),
                      ("Seats", "seats"), ("Status", "get_status_display"), ("Driver", "current_driver"),
                      ("Odometer km", "odometer_km"), ("Insurance expiry", "insurance_expiry"),
                      ("Inspection expiry", "inspection_expiry"), ("Next service", "next_service_due")]

    def get_base_queryset(self):
        return Vehicle.objects.select_related("current_driver")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        soon = timezone.localdate() + timedelta(days=30)
        agg = Vehicle.objects.aggregate(
            total=Count("pk"), available=Count("pk", filter=Q(status=Vehicle.AVAILABLE)),
            out=Count("pk", filter=Q(status=Vehicle.OUT)), maint=Count("pk", filter=Q(status=Vehicle.MAINTENANCE)),
            seats=Sum("seats", filter=Q(status=Vehicle.AVAILABLE)),
            expiring=Count("pk", filter=Q(insurance_expiry__lte=soon) | Q(inspection_expiry__lte=soon)
                           | Q(next_service_due__lte=soon)),
        )
        ctx["kpis"] = agg
        return ctx


class VehicleDetail(ModuleMixin, DetailView):
    model = Vehicle
    template_name = "fleet/vehicle_detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["fuel_logs"] = self.object.fuel_logs.select_related("filled_by")[:20]
        ctx["fuel_total"] = self.object.fuel_logs.aggregate(l=Sum("litres"), a=Sum("amount"))
        ctx["flags"] = self.object.expiry_flags(within_days=60)
        ctx["fuel_form"] = FuelLogForm(initial={"date": timezone.localdate()})
        ctx["maintenance_logs"] = self.object.maintenance_logs.all()[:10]
        ctx["maintenance_form"] = MaintenanceLogForm(initial={"date": timezone.localdate()})
        return ctx


class VehicleCreate(CreateScreen):
    model = Vehicle
    form_class = VehicleForm
    title = "Add vehicle"
    cancel_url_name = "fleet:list"


class VehicleUpdate(UpdateScreen):
    model = Vehicle
    form_class = VehicleForm


@module_required("operations")
def add_fuel(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST" and request.user.can_edit_operations:
        form = FuelLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.vehicle = vehicle
            log.save()
            if log.odometer_km and log.odometer_km > vehicle.odometer_km:
                vehicle.odometer_km = log.odometer_km
                vehicle.save(update_fields=["odometer_km", "updated_at"])
            messages.success(request, "Fuel entry recorded.")
        else:
            messages.error(request, "Fuel entry not saved: check the form.")
    return redirect("fleet:detail", pk=pk)


@module_required("operations")
def add_maintenance(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST" and request.user.can_edit_operations:
        form = MaintenanceLogForm(request.POST)
        if form.is_valid():
            log = form.save(commit=False)
            log.vehicle = vehicle
            log.save()
            fields = ["updated_at"]
            if log.odometer_km and log.odometer_km > vehicle.odometer_km:
                vehicle.odometer_km = log.odometer_km
                fields.append("odometer_km")
            if log.kind in ("service", "inspection"):
                vehicle.last_service_date = log.date
                fields.append("last_service_date")
            if log.next_service_due:
                vehicle.next_service_due = log.next_service_due
                fields.append("next_service_due")
            vehicle.save(update_fields=fields)
            messages.success(request, "Maintenance recorded" + (" and posted to Finance." if log.cost else "."))
        else:
            messages.error(request, "Maintenance not saved: check the form.")
    return redirect("fleet:detail", pk=pk)


@module_required("operations")
def set_status(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    status = request.POST.get("status")
    if request.method == "POST" and request.user.can_edit_operations and status in dict(Vehicle.STATUS_CHOICES):
        vehicle.status = status
        vehicle.save(update_fields=["status", "updated_at", "updated_by"])
        messages.success(request, f"{vehicle.registration_no} is now {vehicle.get_status_display().lower()}.")
    return redirect("fleet:detail", pk=pk)
