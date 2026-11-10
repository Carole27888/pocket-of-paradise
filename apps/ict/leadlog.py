"""ICT Lead Log: every lead from every social channel and the front desk, on one page."""
import django_filters as df
from django import forms
from django.db.models import Count, Q

from apps.accounts import roles
from apps.accounts.permissions import ModuleMixin, pick_module
from apps.core.lists import ListScreen
from apps.leads.models import Enquiry, Stage, source_choices, source_labels

FRONT_DESK = ["walk_in", "call"]
GROUPS = {
    "social": ("Social media", Q(source__in=roles.SOCIAL_SOURCES)),
    "frontdesk": ("Front desk", Q(captured_via="front_desk") | Q(source__in=FRONT_DESK)),
    "whatsapp": ("WhatsApp bot", Q(source="whatsapp")),
    "website": ("Website & Google", Q(source__in=["website", "google"])),
    "other": ("Other & events", ~(Q(source__in=roles.SOCIAL_SOURCES) | Q(source__in=FRONT_DESK) | Q(source__in=["whatsapp", "website", "google"]) | Q(captured_via="front_desk"))),
}


class LeadLogFilter(df.FilterSet):
    channel = df.ChoiceFilter(choices=[(k, v[0]) for k, v in GROUPS.items()], method="by_channel",
                              empty_label="All channels")
    source = df.ChoiceFilter(choices=source_choices, empty_label="All sources")
    stage = df.ChoiceFilter(choices=Stage.choices, empty_label="All stages")
    unassigned = df.ChoiceFilter(choices=[("yes", "Unassigned only")], method="by_unassigned",
                                 empty_label="Assigned & unassigned", label="Assignment")
    created_after = df.DateFilter(field_name="created_at", lookup_expr="date__gte", label="From",
                                  widget=forms.DateInput(attrs={"type": "date"}))
    created_before = df.DateFilter(field_name="created_at", lookup_expr="date__lte", label="To",
                                   widget=forms.DateInput(attrs={"type": "date"}))

    def by_channel(self, qs, name, value):
        return qs.filter(GROUPS[value][1]) if value in GROUPS else qs

    def by_unassigned(self, qs, name, value):
        return qs.filter(assigned_agent__isnull=True) if value == "yes" else qs

    class Meta:
        model = Enquiry
        fields = ["channel", "source", "stage", "unassigned"]


class LeadLog(ListScreen):
    module_key = "ict"
    model = Enquiry
    filterset_class = LeadLogFilter
    search_fields = ["reference", "client__full_name", "client__phone", "client__email", "destination_text",
                     "package__title", "destination__name", "campaign__name"]
    sort_fields = ["created_at", "client__full_name", "stage", "source"]
    default_sort = "-created_at"
    paginate_by = 40
    template_name = "ict/lead_log.html"
    partial_template = "ict/partials/lead_log_table.html"
    title = "Lead Log"
    subtitle = "Every lead from every social channel, the website, WhatsApp and the front desk. One list, one source of truth."
    export_name = "lead-log"
    export_columns = [("Reference", "reference"), ("Received", "created_at"), ("Client", "client.full_name"),
                      ("Phone", "client.phone"), ("Email", "client.email"), ("Channel", "channel_label"),
                      ("Source", "get_source_display"), ("Captured by", "captured_by_label"),
                      ("Campaign", "campaign"), ("Interest", "interest_label"), ("Party", "party_label"),
                      ("Stage", "get_stage_display"), ("Agent", "assigned_agent")]

    def get_base_queryset(self):
        return Enquiry.objects.select_related("client", "package", "destination", "assigned_agent",
                                              "captured_by", "campaign")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = Enquiry.objects.all()
        current = self.request.GET.get("channel", "")
        tiles = [{"key": "", "label": "All leads", "count": base.count(), "on": current == ""}]
        for key, (label, cond) in GROUPS.items():
            tiles.append({"key": key, "label": label, "count": base.filter(cond).count(), "on": current == key})
        ctx["tiles"] = tiles
        ctx["by_source"] = list(base.values("source").annotate(n=Count("pk")).order_by("-n"))
        for row in ctx["by_source"]:
            row["label"] = source_labels().get(row["source"], row["source"])
        ctx["unassigned"] = base.filter(assigned_agent__isnull=True).exclude(stage__in=["lost", "completed"]).count()
        ctx["can_open"] = pick_module(self.request, ("operations", "sales")) is not None
        return ctx
