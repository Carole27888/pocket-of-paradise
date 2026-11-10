import json

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView

from apps.accounts import roles
from apps.accounts.permissions import ModuleMixin, module_required
from apps.core.crud import CreateScreen, UpdateScreen
from apps.core.lists import ListScreen
from apps.leads.models import OPEN_STAGES

from .filters import EmployeeFilter
from .forms import EmployeeForm
from .models import Employee

SENSITIVE_ROLES = (roles.ADMIN, roles.FINANCE, roles.DIRECTOR)


class EmployeeList(ListScreen):
    model = Employee
    filterset_class = EmployeeFilter
    search_fields = ["full_name", "phone", "email", "title", "languages"]
    sort_fields = ["full_name", "title", "on_duty", "department__name"]
    default_sort = "full_name"
    template_name = "people/employee_list.html"
    partial_template = "people/partials/employee_table.html"
    title = "Staff & Guides"
    subtitle = "Consultants, guides, drivers and office staff, with duty status and specialties."
    export_name = "staff"
    export_columns = [("Name", "full_name"), ("Title", "title"), ("Department", "department"), ("Phone", "phone"),
                      ("Email", "email"), ("Roles", "roles_label"), ("Status", "get_on_duty_display")]

    def get_base_queryset(self):
        return (Employee.objects.filter(is_active=True).select_related("department").prefetch_related("specialties")
                .annotate(open_enquiries=Count("enquiries", filter=Q(enquiries__stage__in=OPEN_STAGES))))

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        base = Employee.objects.filter(is_active=True)
        ctx["kpis"] = {
            "staff": base.count(), "agents": base.filter(is_sales_agent=True).count(),
            "guides": base.filter(Q(is_guide=True) | Q(is_driver=True)).count(),
            "out": base.filter(on_duty=Employee.OUT_ON_TRIP).count(),
            "leave": base.filter(on_duty__in=[Employee.OFF, Employee.ON_LEAVE]).count(),
        }
        ctx["duty_choices"] = Employee.DUTY_CHOICES
        return ctx


class EmployeeDetail(ModuleMixin, DetailView):
    model = Employee
    template_name = "people/employee_detail.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["show_sensitive"] = self.request.user.has_role(*SENSITIVE_ROLES)
        ctx["open_enquiries"] = self.object.enquiries.filter(stage__in=OPEN_STAGES).select_related("client")[:10]
        ctx["vehicles"] = self.object.vehicles.all()
        return ctx


class StaffEditMixin:
    model = Employee
    form_class = EmployeeForm
    cancel_url_name = "people:list"

    def dispatch(self, request, *a, **kw):
        if request.user.is_authenticated and not request.user.has_role(roles.ADMIN, roles.OPERATIONS):
            raise PermissionDenied
        return super().dispatch(request, *a, **kw)

    def get_success_url(self):
        from django.urls import reverse

        return reverse("people:detail", args=[self.object.pk])


class EmployeeCreate(StaffEditMixin, CreateScreen):
    title = "Add staff member"


class EmployeeUpdate(StaffEditMixin, UpdateScreen):
    pass


@require_POST
@module_required("operations")
def set_duty(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    if not request.user.can_edit_operations and getattr(request.user, "employee", None) != emp:
        raise PermissionDenied
    status = request.POST.get("on_duty")
    if status in dict(Employee.DUTY_CHOICES):
        emp.on_duty = status
        emp.save(update_fields=["on_duty", "updated_at", "updated_by"])
    resp = render(request, "people/partials/duty_pill.html", {"e": emp, "duty_choices": Employee.DUTY_CHOICES})
    resp["HX-Trigger"] = json.dumps({"toast": {"text": f"{emp.full_name}: {emp.get_on_duty_display()}",
                                               "level": "success"}})
    return resp
