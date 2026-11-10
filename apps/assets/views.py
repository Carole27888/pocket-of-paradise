from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts import roles as R
from apps.accounts.permissions import module_required
from apps.people.models import Employee

from . import services
from .models import DepreciationRun, FixedAsset


@module_required("finance")
def register(request):
    can_edit = request.user.is_superuser or request.user.has_role(*R.FINANCE_EDITORS)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        act = request.POST.get("action")
        try:
            if act == "add":
                emp = Employee.objects.filter(pk=request.POST.get("assigned_to") or 0).first()
                FixedAsset.objects.create(name=request.POST["name"], category=request.POST.get("category", "equipment"), cost=Decimal(request.POST["cost"]),
                                          residual_value=Decimal(request.POST.get("residual") or 0), useful_life_months=int(request.POST.get("life") or 60),
                                          purchase_date=request.POST.get("purchase_date") or timezone.localdate(), assigned_to=emp,
                                          location=request.POST.get("location", ""), serial=request.POST.get("serial", ""), created_by=request.user)
                messages.success(request, "Asset added.")
            elif act == "dispose":
                services.dispose(get_object_or_404(FixedAsset, pk=request.POST.get("pk")), on=timezone.localdate(), value=Decimal(request.POST.get("value") or 0), user=request.user)
            elif act == "depreciate":
                run = services.post_depreciation(request.POST.get("period") or None, request.user)
                messages.success(request, f"Depreciation for {run.period}: KES {run.amount:,.2f}")
        except (ValueError, InvalidOperation, KeyError) as e:
            messages.error(request, str(e) or "Check the numbers.")
        return redirect("assets:register")
    assets = list(FixedAsset.objects.select_related("assigned_to"))
    active = [a for a in assets if a.status == "active"]
    return render(request, "assets/register.html", {
        "assets": assets, "active_count": len(active), "can_edit": can_edit, "employees": Employee.objects.filter(is_active=True), "categories": FixedAsset.CATEGORIES,
        "cost": sum((a.cost for a in active), Decimal("0")), "book": sum((a.book_value() for a in active), Decimal("0")),
        "runs": DepreciationRun.objects.all()[:6], "today": timezone.localdate()})
