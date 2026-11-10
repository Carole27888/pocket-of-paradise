from django.db.models import Count, Max, Q
from django.views.generic import DetailView

from apps.accounts.permissions import ModuleMixin, is_scoped_agent
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.lists import ListScreen

from .filters import ClientFilter
from .forms import ClientForm
from .models import Client


def visible_clients(user):
    qs = Client.objects.alive()
    if is_scoped_agent(user):
        qs = qs.filter(Q(owner__user=user) | Q(enquiries__assigned_agent__user=user)).distinct()
    return qs


class ClientList(ListScreen):
    shared_with = ("sales",)
    model = Client
    filterset_class = ClientFilter
    search_fields = ["full_name", "phone", "alt_phone", "email", "nationality"]
    sort_fields = ["full_name", "created_at", "enquiry_count", "last_enquiry"]
    default_sort = "-created_at"
    template_name = "contacts/client_list.html"
    partial_template = "contacts/partials/client_table.html"
    title = "Clients"
    subtitle = "One record per traveller. Returning clients are matched by phone, email or passport."
    export_name = "clients"
    export_columns = [("Name", "full_name"), ("Phone", "phone"), ("Email", "email"), ("Nationality", "nationality"),
                      ("International", "is_international"), ("Enquiries", "enquiry_count"),
                      ("Added", "created_at")]

    def get_base_queryset(self):
        return visible_clients(self.request.user).annotate(
            enquiry_count=Count("enquiries"), last_enquiry=Max("enquiries__created_at"))


class ClientDetail(ModuleMixin, DetailView):
    shared_with = ("sales",)
    model = Client
    template_name = "contacts/client_detail.html"

    def get_queryset(self):
        return visible_clients(self.request.user)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        from apps.leads.views import visible_enquiries

        ctx["enquiries"] = visible_enquiries(self.request.user).filter(client=self.object)
        ctx["companion_trips"] = self.object.trips_as_companion.select_related("enquiry")
        return ctx


class _NotifyOwnerMixin:
    def form_valid(self, form):
        resp = super().form_valid(form)
        owner = self.object.owner
        if owner and owner.user_id and owner.user_id != self.request.user.pk and form.owner_changed:
            from apps.notifications.services import notify

            notify(owner.user, f"New client: {self.object.full_name}", "This client has been assigned to you.",
                   self.object.get_absolute_url(), "info", dedupe_key=f"client-owner:{self.object.pk}:{owner.pk}")
        return resp


class ClientCreate(_NotifyOwnerMixin, CreateScreen):
    shared_with = ("sales",)
    edit_permission = "can_manage_clients"
    model = Client
    form_class = ClientForm
    title = "New client"
    cancel_url_name = "contacts:list"


class ClientUpdate(_NotifyOwnerMixin, UpdateScreen):
    shared_with = ("sales",)
    edit_permission = "can_manage_clients"
    model = Client
    form_class = ClientForm
