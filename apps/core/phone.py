"""Phone number normalisation. Kenyan numbers become +2547XXXXXXXX / +2541XXXXXXXX."""
import re

_digits = re.compile(r"\D+")


def normalise_phone(raw: str | None) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    plus = raw.startswith("+") or raw.startswith("00")
    digits = _digits.sub("", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    if not digits:
        return ""
    if plus:
        return "+" + digits
    # Kenyan local formats: 07XXXXXXXX, 01XXXXXXXX, 7XXXXXXXX, 2547XXXXXXXX
    if digits.startswith("254") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+254" + digits[1:]
    if len(digits) == 9 and digits[0] in "17":
        return "+254" + digits
    return "+" + digits


def is_kenyan(phone: str) -> bool:
    return normalise_phone(phone).startswith("+254")


def whatsapp_link(phone: str) -> str:
    p = normalise_phone(phone)
    return f"https://wa.me/{p.lstrip('+')}" if p else ""


def display_phone(phone: str) -> str:
    p = normalise_phone(phone)
    if p.startswith("+254") and len(p) == 13:
        return f"+254 {p[4:7]} {p[7:10]} {p[10:]}"
    return p
