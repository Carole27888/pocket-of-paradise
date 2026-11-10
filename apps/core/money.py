from decimal import ROUND_HALF_UP, Decimal

SYMBOLS = {"KES": "KES ", "USD": "$", "EUR": "€", "GBP": "£"}


def format_money(amount, currency: str = "KES", decimals: int | None = None) -> str:
    if amount is None or amount == "":
        return "—"
    amount = Decimal(str(amount))
    if decimals is None:
        decimals = 0 if currency == "KES" or amount == amount.to_integral_value() else 2
    q = Decimal(1).scaleb(-decimals)
    amount = amount.quantize(q, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    body = f"{abs(amount):,.{decimals}f}"
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{body}"


def compact_money(amount, currency: str = "KES") -> str:
    """KES 186.4M / KES 3.84M / KES 485K."""
    if amount is None:
        return "—"
    a = Decimal(str(amount))
    sym = SYMBOLS.get(currency, currency + " ")
    for div, suf in ((Decimal("1e9"), "B"), (Decimal("1e6"), "M"), (Decimal("1e3"), "K")):
        if abs(a) >= div:
            v = a / div
            txt = f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"
            return f"{sym}{txt.rstrip('0').rstrip('.')}{suf}"
    return format_money(a, currency)


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _below_thousand(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " hundred")
        n %= 100
        if n:
            out.append("and")
    if n >= 20:
        out.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(amount, currency: str = "KES") -> str:
    """4,850.50 -> 'Kenya Shillings four thousand, eight hundred and fifty and 50/100 only'."""
    names = {"KES": "Kenya Shillings", "USD": "US Dollars", "EUR": "Euros", "GBP": "Pounds Sterling"}
    amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = int(amount), int((amount - int(amount)) * 100)
    if whole == 0:
        words = "zero"
    else:
        parts = []
        for size, label in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
            if whole >= size:
                parts.append(_below_thousand(whole // size) + f" {label}")
                whole %= size
        if whole:
            parts.append(_below_thousand(whole))
        words = ", ".join(parts)
    tail = f" and {cents:02d}/100" if cents else ""
    return f"{names.get(currency, currency)} {words}{tail} only".replace("  ", " ")
