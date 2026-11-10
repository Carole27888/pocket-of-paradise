"""Generic list screen: search, django-filter, sort, paginate, CSV/Excel export, HTMX partial."""
import csv
import io

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.views.generic import TemplateView

from apps.accounts.permissions import ModuleMixin


class ListScreen(ModuleMixin, TemplateView):
    model = None
    filterset_class = None
    search_fields: list[str] = []
    sort_fields: list[str] = []
    default_sort = "-pk"
    paginate_by = 25
    export_columns: list[tuple[str, str]] = []  # (header, attribute path or callable name)
    export_name = "export"
    template_name = ""
    partial_template = ""
    title = ""
    subtitle = ""

    def get_base_queryset(self):
        qs = self.model._default_manager.all()
        if hasattr(qs, "alive"):
            qs = qs.alive()
        return qs

    def get_queryset(self):
        qs = self.get_base_queryset()
        term = self.request.GET.get("q", "").strip()
        if term and self.search_fields:
            cond = Q()
            for f in self.search_fields:
                cond |= Q(**{f"{f}__icontains": term})
            qs = qs.filter(cond)
        self.filterset = self.filterset_class(self.request.GET, queryset=qs) if self.filterset_class else None
        if self.filterset is not None:
            qs = self.filterset.qs
        sort = self.request.GET.get("sort", "")
        if sort.lstrip("-") in self.sort_fields:
            qs = qs.order_by(sort, "-pk")
        else:
            qs = qs.order_by(self.default_sort)
        return qs.distinct() if term else qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        page = Paginator(qs, self.paginate_by).get_page(self.request.GET.get("page"))
        ctx.update(page_obj=page, object_list=page.object_list, filter=self.filterset, title=self.title,
                   subtitle=self.subtitle, total=page.paginator.count, q=self.request.GET.get("q", ""))
        return ctx

    def get(self, request, *args, **kwargs):
        fmt = request.GET.get("export")
        if fmt in ("csv", "xlsx"):
            return self.export(fmt)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        if self.request.htmx and not self.request.htmx.boosted and self.partial_template:
            return [self.partial_template]
        return [self.template_name]

    # ---- export
    @staticmethod
    def _resolve(obj, path):
        val = obj
        for part in path.split("."):
            val = getattr(val, part, "") if val is not None else ""
            if callable(val):
                val = val()
        return "" if val is None else val

    def export(self, fmt):
        rows = [[self._resolve(o, p) for _, p in self.export_columns] for o in self.get_queryset()[:20000]]
        headers = [h for h, _ in self.export_columns]
        if fmt == "csv":
            resp = HttpResponse(content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="{self.export_name}.csv"'
            w = csv.writer(resp)
            w.writerow(headers)
            w.writerows([[str(c) for c in r] for r in rows])
            return resp
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        wb = Workbook()
        ws = wb.active
        ws.title = self.export_name[:30]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="4F1410")
        for r in rows:
            ws.append([c if isinstance(c, (int, float)) else str(c) for c in r])
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(40, max(10, *(len(str(c.value or "")) for c in col)))
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(buf.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{self.export_name}.xlsx"'
        return resp
