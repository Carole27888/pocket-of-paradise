"""Agent commission: what each sales agent earned on their bookings in a month, at the commission rate set in Tax Settings.
Payroll uses the same sum, so this page shows what the next payroll run will add."""
import csv
import calendar as cal
from datetime import date

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.permissions import module_required
from apps.people.models import Employee
from apps.sales.models import Booking, BookingStatus

from . import payroll as payroll_calc
from .models import PayrollLine, q2
from apps.sales.services import sum_kes

FIN = "finance"


def _month(request):
    today = timezone.localdate()
    try:
        y, m = (request.GET.get("month") or f"{today:%Y-%m}").split("-")
        return date(int(y), int(m), 1)
    except ValueError:
        return today.replace(day=1)


def rows_for(first):
    last = first.replace(day=cal.monthrange(first.year, first.month)[1])
    rate = payroll_calc.load_rates()["sales_commission_rate"]
    out, total = [], 0
    for emp in Employee.objects.filter(is_sales_agent=True, is_active=True).order_by("full_name"):
        won = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(agent=emp, booked_at__date__gte=first, booked_at__date__lte=last)
        revenue = sum_kes(won)
        commission = q2(revenue * rate / 100)
        paid = PayrollLine.objects.filter(employee=emp, run__period=first).values_list("commission", flat=True).first()
        out.append({"agent": emp, "bookings": won.count(), "revenue": revenue, "commission": commission, "in_payroll": paid})
        total += commission
    return out, rate, total


@module_required(FIN)
def commission(request):
    first = _month(request)
    rows, rate, total = rows_for(first)
    if request.GET.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="commission-{first:%Y-%m}.csv"'
        w = csv.writer(resp)
        w.writerow(["Agent", "Bookings", "Revenue (KES)", f"Commission at {rate}% (KES)"])
        for r in rows:
            w.writerow([r["agent"].full_name, r["bookings"], r["revenue"], r["commission"]])
        return resp
    py, pm = (first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12)
    ny, nm = (first.year, first.month + 1) if first.month < 12 else (first.year + 1, 1)
    return render(request, "finance/commission.html", {
        "rows": rows, "rate": rate, "total": total, "first": first, "prev": f"{py}-{pm:02d}", "next": f"{ny}-{nm:02d}",
        "revenue_total": sum(r["revenue"] for r in rows), "agent_count": sum(1 for r in rows if r["bookings"])})
