from django.utils import timezone

from apps.core.jobs import daily, task
from apps.core.models import Currency, ExchangeRate

from . import fx, messaging, social  # noqa: F401  (registers FX, messaging and social/ads mocks)
from .base import get_service


@daily(hour=6, minute=0)
@task("integrations.refresh_exchange_rates")
def refresh_exchange_rates():
    codes = list(Currency.objects.filter(is_active=True, is_base=False).values_list("code", flat=True))
    if not codes:
        return "no currencies"
    rates = get_service("fx").latest(codes)
    today = timezone.localdate()
    for code, rate in rates.items():
        ExchangeRate.objects.update_or_create(currency_id=code, date=today,
                                              defaults={"rate_to_base": rate, "source": get_service("fx").mode})
    return ", ".join(f"{c}={r}" for c, r in rates.items())
