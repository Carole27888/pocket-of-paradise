"""Kenyan payroll calculator. Every rate comes from TaxSetting; nothing is hard-coded."""
from decimal import Decimal

from .models import TaxSetting, q2

D = Decimal


def load_rates() -> dict:
    from .services import ensure_tax_settings

    ensure_tax_settings()
    out = {}
    for t in TaxSetting.objects.all():
        out[t.key] = t.data if t.data is not None else (t.value if t.value is not None else D("0"))
    return out


def nssf(gross: D, r: dict) -> D:
    """Employee NSSF (employer pays the same): rate on pensionable pay between 0 and the upper limit."""
    pensionable = min(gross, r["nssf_uel"])
    return q2(pensionable * r["nssf_rate"] / 100)


def shif(gross: D, r: dict) -> D:
    return max(q2(gross * r["shif_rate"] / 100), r["shif_min"]) if gross > 0 else D("0")


def ahl(gross: D, r: dict) -> D:
    return q2(gross * r["ahl_rate"] / 100)


def paye(taxable: D, r: dict) -> D:
    """Progressive tax on taxable pay less personal relief (never negative)."""
    tax, left = D("0"), max(taxable, D("0"))
    for width, rate in r["paye_bands"]:
        if left <= 0:
            break
        chunk = left if width is None else min(left, D(str(width)))
        tax += chunk * D(str(rate)) / 100
        left -= chunk
    return max(q2(tax - r["personal_relief"]), D("0"))


def calculate(basic: D, allowances: D = D("0"), commission: D = D("0"), other_deductions: D = D("0"),
              rates: dict | None = None) -> dict:
    r = rates or load_rates()
    basic, allowances, commission = D(basic), D(allowances), D(commission)
    gross = q2(basic + allowances + commission)
    n, s, a = nssf(gross, r), shif(gross, r), ahl(gross, r)
    taxable = max(gross - n - s - a, D("0"))
    p = paye(taxable, r)
    net = q2(gross - n - s - a - p - D(other_deductions))
    return {"basic": q2(basic), "allowances": q2(allowances), "commission": q2(commission), "gross": gross,
            "nssf": n, "shif": s, "ahl": a, "nssf_employer": n, "ahl_employer": a, "taxable": q2(taxable),
            "paye": p, "other_deductions": q2(other_deductions), "net": net}
