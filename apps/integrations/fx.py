import json
import random
import urllib.request
from decimal import Decimal

from django.conf import settings

from .base import BaseService, register

MOCK_BASE = {"USD": Decimal("129.20"), "EUR": Decimal("140.60"), "GBP": Decimal("164.30")}


class FXRateService(BaseService):
    def latest(self, codes: list[str]) -> dict[str, Decimal]:
        """KES per 1 unit of each currency."""
        raise NotImplementedError


@register("fx", "mock")
class MockFXRateService(FXRateService):
    def latest(self, codes):
        out = {}
        for c in codes:
            base = MOCK_BASE.get(c, Decimal("100"))
            jitter = Decimal(str(round(random.uniform(-0.004, 0.004), 5)))
            out[c] = (base * (1 + jitter)).quantize(Decimal("0.0001"))
        self.log("latest", detail=json.dumps({k: str(v) for k, v in out.items()}))
        return out


@register("fx", "live")
class LiveFXRateService(FXRateService):
    """exchangerate-api.com style provider: GET /v6/<key>/latest/KES."""

    URL = "https://v6.exchangerate-api.com/v6/{key}/latest/KES"

    def latest(self, codes):
        with urllib.request.urlopen(self.URL.format(key=settings.FX_PROVIDER_API_KEY), timeout=20) as r:
            data = json.loads(r.read())
        rates = data["conversion_rates"]
        out = {c: (Decimal(1) / Decimal(str(rates[c]))).quantize(Decimal("0.0001")) for c in codes if c in rates}
        self.log("latest", detail=json.dumps({k: str(v) for k, v in out.items()}))
        return out
