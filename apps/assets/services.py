from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.finance import services as fin

from .models import DepreciationRun, FixedAsset


@transaction.atomic
def post_depreciation(period: str | None = None, user=None) -> DepreciationRun:
    """Post one month's straight-line depreciation for all active assets (re-running a period changes nothing)."""
    today = timezone.localdate()
    period = period or today.strftime("%Y-%m")
    existing = DepreciationRun.objects.filter(period=period).first()
    if existing:
        return existing
    y, m = (int(x) for x in period.split("-"))
    total = Decimal("0")
    for a in FixedAsset.objects.filter(status=FixedAsset.ACTIVE):
        # a month is charged from the month after purchase until the cost less residual is used up
        idx = (y - a.purchase_date.year) * 12 + m - a.purchase_date.month
        if 1 <= idx <= a.useful_life_months:
            total += a.monthly_depreciation
    run = DepreciationRun.objects.create(period=period, amount=total)
    if total > 0:
        from datetime import date
        from calendar import monthrange

        fin.post(date(y, m, monthrange(y, m)[1]), f"Depreciation {period}", [("exp_depreciation", total, 0), ("accum_depreciation", 0, total)],
                 source=("depreciation", int(f"{y}{m:02d}"), "month"), user=user)
    return run


@transaction.atomic
def dispose(asset: FixedAsset, *, on, value=Decimal("0"), user=None) -> FixedAsset:
    if asset.status == FixedAsset.DISPOSED:
        raise ValueError("Already disposed.")
    asset.status, asset.disposed_on, asset.disposal_value = FixedAsset.DISPOSED, on, value
    asset.save()
    return asset
