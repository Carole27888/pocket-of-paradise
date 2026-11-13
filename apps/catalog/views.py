from django.contrib import messages
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import DetailView

from apps.accounts.permissions import ModuleMixin, module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.lists import ListScreen
from apps.leads.models import OPEN_STAGES

from .filters import AccommodationFilter, DestinationFilter, PackageFilter
from .forms import (AccommodationForm, DestinationForm, ItineraryDayFormSet, PackageForm, RoomRateFormSet)
from .models import Accommodation, Destination, Itinerary, TourPackage


def _can_add_photos(user) -> bool:
    from .library import _can_edit

    return _can_edit(user)


# ------------------------------------------------------------------ destinations
class DestinationList(ListScreen):
    shared_with = ("sales",)
    model = Destination
    filterset_class = DestinationFilter
    search_fields = ["name", "summary", "activities"]
    sort_fields = ["name", "region", "package_count", "enquiry_count"]
    default_sort = "name"
    paginate_by = 24
    template_name = "catalog/destination_list.html"
    partial_template = "catalog/partials/destination_grid.html"
    title = "Destinations"
    subtitle = "Parks, beaches and cross-border add-ons we sell, with where they sit on the map."
    export_name = "destinations"
    export_columns = [("Name", "name"), ("Region", "get_region_display"), ("Country", "get_country_display"),
                      ("Best time", "best_time_to_visit"), ("Packages", "package_count"),
                      ("Open enquiries", "enquiry_count")]

    def get_base_queryset(self):
        return Destination.objects.alive().annotate(
            package_count=Count("packages", filter=Q(packages__deleted_at__isnull=True), distinct=True),
            enquiry_count=Count("enquiries", filter=Q(enquiries__stage__in=OPEN_STAGES), distinct=True),
        )

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        return ctx


class DestinationDetail(ModuleMixin, DetailView):
    shared_with = ("sales",)
    model = Destination
    template_name = "catalog/destination_detail.html"
    slug_field = "slug"

    def get_queryset(self):
        return Destination.objects.alive()

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        d = self.object
        ctx["packages"] = d.packages.alive().order_by("status", "code")
        ctx["accommodations"] = d.accommodations.alive()
        ctx["recent_enquiries"] = d.enquiries.select_related("client", "assigned_agent").order_by("-created_at")[:8]
        ctx["open_enquiries"] = d.enquiries.filter(stage__in=OPEN_STAGES).count()
        ctx["can_add_photos"] = _can_add_photos(self.request.user)
        return ctx


class _PhotoMessage:
    def form_valid(self, form):
        resp = super().form_valid(form)
        added, skipped = getattr(form, "photos_added", 0), getattr(form, "photos_skipped", 0)
        if added:
            messages.success(self.request, f"{added} photo{'s' if added != 1 else ''} added.")
        if skipped:
            messages.error(self.request, f"{skipped} file{'s' if skipped != 1 else ''} skipped: not a picture.")
        return resp


class DestinationCreate(_PhotoMessage, CreateScreen):
    model = Destination
    form_class = DestinationForm
    title = "New destination"
    cancel_url_name = "catalog:destinations"
    success_message = "Destination saved."


class DestinationUpdate(_PhotoMessage, UpdateScreen):
    model = Destination
    form_class = DestinationForm
    slug_field = "slug"
    success_message = "Destination updated."


# ------------------------------------------------------------------ packages
class PackageList(ListScreen):
    shared_with = ("sales",)
    model = TourPackage
    filterset_class = PackageFilter
    search_fields = ["code", "title", "summary", "destinations__name"]
    sort_fields = ["code", "title", "price_per_person", "days", "status", "enquiry_count"]
    default_sort = "code"
    template_name = "catalog/package_list.html"
    partial_template = "catalog/partials/package_table.html"
    title = "Tour Packages"
    subtitle = "Group joining, private, custom and day-trip packages with pricing and itineraries."
    export_name = "tour-packages"
    export_columns = [("Code", "code"), ("Title", "title"), ("Type", "get_package_type_display"),
                      ("Duration", "duration_label"), ("Currency", "currency"), ("Price pp", "price_per_person"),
                      ("Single supplement", "single_supplement"), ("Child rate", "child_rate"),
                      ("Status", "get_status_display"), ("Open enquiries", "enquiry_count")]

    def get_base_queryset(self):
        return (TourPackage.objects.alive().prefetch_related("destinations")
                .annotate(enquiry_count=Count("enquiries", filter=Q(enquiries__stage__in=OPEN_STAGES), distinct=True)))

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = TourPackage.objects.alive()
        ctx["kpis"] = {
            "published": base.filter(status="published").count(),
            "seasonal": base.filter(status="seasonal").count(),
            "draft": base.filter(status="draft").count(),
            "total": base.count(),
        }
        return ctx


class PackageDetail(ModuleMixin, DetailView):
    shared_with = ("sales",)
    model = TourPackage
    template_name = "catalog/package_detail.html"
    slug_field = "slug"

    def get_queryset(self):
        return TourPackage.objects.alive().prefetch_related("destinations")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        p = self.object
        days = []
        if p.itinerary_id:
            days = list(p.itinerary.days.select_related("accommodation", "destination"))
        ctx["days"] = days
        ctx["enquiries"] = p.enquiries.select_related("client", "assigned_agent").order_by("-created_at")[:10]
        ctx["open_enquiries"] = p.enquiries.filter(stage__in=OPEN_STAGES).count()
        return ctx


class PackageCreate(CreateScreen):
    edit_required = True
    edit_permission = "can_edit_packages"
    shared_with = ("sales",)   # a Sales Manager adds packages from the Sales module
    model = TourPackage
    form_class = PackageForm
    title = "New tour package"
    cancel_url_name = "catalog:packages"
    success_message = "Package saved. Add the day-by-day itinerary next."

    def get_success_url(self):
        return reverse("catalog:package_itinerary", args=[self.object.slug])


class PackageUpdate(UpdateScreen):
    edit_required = True
    edit_permission = "can_edit_packages"
    shared_with = ("sales",)   # a Sales Manager adds packages from the Sales module
    model = TourPackage
    form_class = PackageForm
    slug_field = "slug"
    success_message = "Package updated."


@module_required("operations", "sales")
def package_itinerary(request, slug):
    package = get_object_or_404(TourPackage.objects.alive(), slug=slug)
    if not request.user.can_edit_packages:
        return redirect(package)
    if package.itinerary_id is None:
        package.itinerary = Itinerary.objects.create(title=package.title)
        package.save(update_fields=["itinerary"])
    formset = ItineraryDayFormSet(request.POST or None, instance=package.itinerary, prefix="days")
    if request.method == "POST" and formset.is_valid():
        formset.save()
        messages.success(request, "Itinerary saved.")
        return redirect(package)
    return render(request, "catalog/itinerary_form.html", {"package": package, "formset": formset})


# ------------------------------------------------------------------ accommodations
class AccommodationList(ListScreen):
    model = Accommodation
    filterset_class = AccommodationFilter
    search_fields = ["name", "destination__name", "contact_name"]
    sort_fields = ["name", "destination__name", "accommodation_type", "star_rating"]
    default_sort = "destination__name"
    template_name = "catalog/accommodation_list.html"
    partial_template = "catalog/partials/accommodation_table.html"
    title = "Accommodations"
    subtitle = "Lodges, tented camps and hotels we contract, with seasonal rates."
    export_name = "accommodations"
    export_columns = [("Name", "name"), ("Destination", "destination.name"), ("Type", "get_accommodation_type_display"),
                      ("Stars", "star_rating"), ("Contact", "contact_name"), ("Phone", "contact_phone"),
                      ("Email", "contact_email")]

    def get_base_queryset(self):
        return Accommodation.objects.alive().select_related("destination").annotate(rate_count=Count("rates"))


class AccommodationDetail(ModuleMixin, DetailView):
    model = Accommodation
    template_name = "catalog/accommodation_detail.html"

    def get_queryset(self):
        return Accommodation.objects.alive().select_related("destination")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["can_add_photos"] = _can_add_photos(self.request.user)
        return ctx


class AccommodationCreate(_PhotoMessage, CreateScreen):
    model = Accommodation
    form_class = AccommodationForm
    title = "New accommodation"
    cancel_url_name = "catalog:accommodations"
    success_message = "Accommodation saved."


class AccommodationUpdate(_PhotoMessage, UpdateScreen):
    model = Accommodation
    form_class = AccommodationForm
    success_message = "Accommodation updated."


def _add_photos(request, obj):
    """Add photos (with tags) straight from a destination's or accommodation's page."""
    from django.core.exceptions import PermissionDenied

    from .library import _can_edit

    if request.method != "POST" or not _can_edit(request.user):
        raise PermissionDenied
    added, skipped = obj.add_photos(request.FILES.getlist("photos"), request.POST.get("tags", ""))
    if added:
        messages.success(request, f"{added} photo{'s' if added != 1 else ''} added to {obj.name}.")
    if skipped:
        messages.error(request, f"{skipped} file{'s' if skipped != 1 else ''} skipped: not a picture.")
    if not added and not skipped:
        messages.error(request, "Choose at least one photo.")
    return redirect(obj)


@module_required("operations", "sales")
def accommodation_photos(request, pk):
    return _add_photos(request, get_object_or_404(Accommodation.objects.alive(), pk=pk))


@module_required("operations", "sales")
def destination_photos(request, slug):
    return _add_photos(request, get_object_or_404(Destination.objects.alive(), slug=slug))


@module_required("operations")
def accommodation_delete(request, pk):
    """Remove an accommodation from lists and dropdowns. Itineraries, quotations and vouchers that already use it keep it."""
    from django.core.exceptions import PermissionDenied

    from apps.system.services import audit

    acc = get_object_or_404(Accommodation.objects.alive(), pk=pk)
    if request.method != "POST" or not request.user.can_edit_operations:
        raise PermissionDenied
    acc.soft_delete()
    audit(request.user, "accommodation.deleted", acc, f"{acc.name} ({acc.destination.name}) removed", request)
    messages.success(request, f"{acc.name} deleted. Itineraries and quotations that already use it are unchanged.")
    return redirect("catalog:accommodations")


@module_required("operations")
def accommodation_rates(request, pk):
    acc = get_object_or_404(Accommodation.objects.alive(), pk=pk)
    if not request.user.can_edit_operations:
        return redirect(acc)
    formset = RoomRateFormSet(request.POST or None, instance=acc, prefix="rates")
    for f in formset.forms:
        for field in f.fields.values():
            field.widget.attrs.setdefault("class", "input")
    if request.method == "POST" and formset.is_valid():
        formset.save()
        messages.success(request, "Rates saved.")
        return redirect(acc)
    return render(request, "catalog/rates_form.html", {"object": acc, "formset": formset})
