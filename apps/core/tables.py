"""Declarative list screens: describe the columns once, get a searchable/filterable/sortable/exportable table.

    class InvoiceList(TableScreen):
        columns = [Col("Invoice", "number", sort="number", link=True), Col("Total", "total", kind="money", ...)]
"""
from dataclasses import dataclass
from typing import Any, Callable

from django.db.models import Count
from django.utils.html import format_html

from apps.core.lists import ListScreen
from apps.core.money import compact_money, format_money


@dataclass
class Col:
    label: str
    attr: str = ""                       # dotted attribute path (callables are called)
    sort: str = ""                       # queryset field to sort by ("" = not sortable)
    kind: str = "text"                   # text | money | compact | date | datetime | pill | num | bool | pct
    link: bool = False                   # link the cell to obj.get_absolute_url()
    sub: str = ""                        # attribute for a muted second line
    tone: Any = None                     # pill: dict {value: tone} or callable(obj) -> tone
    currency: str = ""                   # money: attribute naming the currency ("" = KES)
    hide_phone: bool = False
    css: str = ""
    export: bool = True
    action: Any = None                   # kind="action": callable(obj, request) -> {"url","label","confirm"} (POST button) or None to hide


def _dig(obj, path):
    val = obj
    for part in path.split("."):
        val = getattr(val, part, "") if val is not None else ""
        if callable(val):
            val = val()
    return "" if val is None else val


def build_cell(col: Col, obj, request=None) -> dict:
    raw = _dig(obj, col.attr) if col.attr else ""
    cell = {"kind": col.kind, "css": col.css, "label": col.label, "tone": "", "url": ""}
    if col.kind == "action":
        cell["text"], cell["sub"], cell["action"] = "", "", (col.action(obj, request) if col.action else None)
        return cell
    if col.kind == "money":
        cur = _dig(obj, col.currency) if col.currency else "KES"
        cell["text"] = format_money(raw, cur or "KES") if raw != "" else "—"
    elif col.kind == "compact":
        cell["text"] = compact_money(raw) if raw != "" else "—"
    elif col.kind == "pct":
        cell["text"] = f"{raw}%" if raw != "" else "—"
    elif col.kind == "bool":
        cell["text"] = "Yes" if raw else "No"
    elif col.kind in ("date", "datetime"):
        from apps.core.templatetags.ds import ds_date, ds_datetime

        cell["text"] = (ds_date if col.kind == "date" else ds_datetime)(raw or None)
    else:
        cell["text"] = str(raw) if raw != "" else "—"
    if col.kind == "pill":
        tone = col.tone(obj) if callable(col.tone) else (col.tone or {}).get(getattr(obj, col.attr.split(".")[0], ""), "neutral")
        cell["tone"] = tone or "neutral"
    if col.link and hasattr(obj, "get_absolute_url"):
        cell["url"] = obj.get_absolute_url()
    cell["sub"] = str(_dig(obj, col.sub)) if col.sub and _dig(obj, col.sub) != "" else ""
    return cell


class TableScreen(ListScreen):
    """ListScreen + declarative columns, KPI strip, status tabs and header actions."""

    template_name = "components/table_page.html"
    partial_template = "components/table_partial.html"
    columns: list[Col] = []
    tabs_field = ""                      # model field shown as tabs with counts (GET ?<field>=value)
    tabs_choices: Any = None             # Choices class / list of (value, label)
    empty_title = "Nothing here yet"
    empty_text = ""
    empty_icon = "inbox"
    placeholder = "Search…"

    def kpis(self, qs) -> list[dict]:
        return []

    def actions(self) -> list[dict]:
        return []                        # [{"label","url","style":"primary|accent|secondary","icon"}]

    def get_base_queryset(self):
        qs = super().get_base_queryset()
        self._unfiltered = qs
        if self.tabs_field:
            val = self.request.GET.get(self.tabs_field, "")
            if val:
                qs = qs.filter(**{self.tabs_field: val})
        return qs

    @property
    def export_columns(self):  # type: ignore[override]
        return [(c.label, c.attr) for c in self.columns if c.export and c.attr]

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["columns"] = self.columns
        ctx["rows"] = [{"obj": o, "cells": [build_cell(c, o, self.request) for c in self.columns]} for o in ctx["object_list"]]
        base = getattr(self, "_unfiltered", None)
        if base is None:
            base = self.get_base_queryset()
        if self.tabs_field and self.tabs_choices:
            counts = dict(base.values_list(self.tabs_field).annotate(n=Count("pk")))
            cur = self.request.GET.get(self.tabs_field, "")
            ctx["tabs"] = [{"key": "", "label": "All", "count": sum(counts.values()), "active": not cur}] + [
                {"key": v, "label": l, "count": counts.get(v, 0), "active": v == cur}
                for v, l in self.tabs_choices]
            ctx["tabs_field"] = self.tabs_field
        cards = list(self.kpis(base))
        if cards:   # a row of stats is always four wide: top it up with plain counts
            name = str(self.model._meta.verbose_name_plural).capitalize()
            extras = [{"label": f"All {name.lower()}", "value": base.count()},
                      {"label": "Matching your filters", "value": ctx.get("total", 0)},
                      {"label": "Shown on this page", "value": len(ctx["object_list"])}]
            for e in extras:
                if len(cards) < 4:
                    cards.append(e)
        ctx["kpi_cards"] = cards
        ctx["header_actions"] = self.actions()
        ctx.update(empty_title=self.empty_title, empty_text=self.empty_text, empty_icon=self.empty_icon,
                   placeholder=self.placeholder, sortable={c.label: c.sort for c in self.columns})
        return ctx
