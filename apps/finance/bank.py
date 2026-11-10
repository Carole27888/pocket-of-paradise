"""Bank statement import and reconciliation for the bank and M-PESA accounts.

1. Import the bank's statement (CSV or Excel). Common Kenyan bank column names are recognised.
   Re-importing an overlapping statement skips lines already there.
2. Auto-match: each statement line to the one entry in the books on that account with the same amount within a few days.
3. The rest: match by hand, or ignore with a reason.
4. Reconcile: statement balance + deposits in transit - outstanding payments must equal the balance in the books.
"""
import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.system.imports import read_table

from .models import Account, BankReconciliation, BankStatementLine, JournalLine

ZERO = Decimal("0")
MATCH_DAYS = 5
DATE_ALIASES = ("date", "transaction_date", "trans_date", "txn_date", "posting_date", "value_date", "booking_date")
DESC_ALIASES = ("description", "narration", "narrative", "particulars", "details", "transaction_details", "remarks")
REF_ALIASES = ("reference", "ref", "ref_no", "reference_no", "cheque_no", "chq_no", "transaction_reference", "transaction_id")
DEBIT_ALIASES = ("debit", "debits", "withdrawal", "withdrawals", "money_out", "paid_out", "dr", "debit_amount")
CREDIT_ALIASES = ("credit", "credits", "deposit", "deposits", "money_in", "paid_in", "cr", "credit_amount")
AMOUNT_ALIASES = ("amount", "transaction_amount", "value")
BALANCE_ALIASES = ("balance", "running_balance", "closing_balance", "ledger_balance")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y", "%d/%m/%y")


class BankError(ValueError):
    pass


def bank_accounts():
    return Account.objects.filter(key__in=("bank", "mpesa"), is_active=True).order_by("code")


def _pick(row, aliases):
    for a in aliases:
        if row.get(a) not in ("", None):
            return str(row[a]).strip()
    return ""


def _money(v):
    v = (v or "").replace(",", "").replace("KES", "").replace("Ksh", "").strip()
    if not v or v in ("-", "—"):
        return None
    neg = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    if v.upper().endswith(("DR", "CR")):
        neg, v = neg or v.upper().endswith("DR"), v[:-2].strip()
    try:
        d = Decimal(v)
    except InvalidOperation:
        raise BankError(f"'{v}' is not an amount.")
    return -d if neg else d


def _date(v):
    if hasattr(v, "date"):
        return v.date()
    v = str(v or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise BankError(f"'{v}' is not a date.")


def parse(rows):
    lines, errors = [], []
    for n, r in enumerate(rows, start=2):
        try:
            d = _date(r.get(next((a for a in DATE_ALIASES if r.get(a)), ""), ""))
            debit, credit = _money(_pick(r, DEBIT_ALIASES)), _money(_pick(r, CREDIT_ALIASES))
            amount = _money(_pick(r, AMOUNT_ALIASES))
            if amount is None:
                if debit is None and credit is None:
                    raise BankError("no amount")
                amount = (credit or ZERO) - (debit or ZERO)
            lines.append({"date": d, "description": _pick(r, DESC_ALIASES)[:255], "reference": _pick(r, REF_ALIASES)[:80],
                          "amount": amount, "balance": _money(_pick(r, BALANCE_ALIASES))})
        except BankError as exc:
            errors.append(f"row {n}: {exc}")
    return lines, errors


def _fp(account, ln, seq):
    raw = f"{account.pk}|{ln['date']}|{ln['amount']}|{ln['reference']}|{ln['description']}|{seq}"
    return hashlib.sha256(raw.encode()).hexdigest()


def import_statement(account, filename, raw, user=None):
    try:
        rows = read_table(filename, raw)
    except ValueError as exc:
        raise BankError(str(exc)) from exc
    lines, errors = parse(rows)
    if not lines:
        raise BankError("No usable rows. The file needs a date column and either debit/credit or amount columns.")
    batch, created, dupes, seen = uuid.uuid4().hex[:8], 0, 0, {}
    with transaction.atomic():
        for ln in lines:
            key = (ln["date"], ln["amount"], ln["reference"], ln["description"])
            seen[key] = seen.get(key, 0) + 1
            fp = _fp(account, ln, seen[key])
            _, made = BankStatementLine.objects.get_or_create(account=account, fingerprint=fp, defaults={**ln, "batch": batch})
            created += made
            dupes += not made
    return {"created": created, "duplicates": dupes, "errors": errors, "matched": auto_match(account, user)}


def _book_amount(jl):
    return jl.debit - jl.credit


def candidates(line, days=MATCH_DAYS):
    """Entries in the books on this account with the same amount, near the date, not already matched."""
    qs = JournalLine.objects.filter(account=line.account, statement_line__isnull=True,
                                    entry__date__gte=line.date - timedelta(days=days), entry__date__lte=line.date + timedelta(days=days)
                                    ).select_related("entry")
    out = [jl for jl in qs if _book_amount(jl) == line.amount]
    ref = (line.reference or "").lower()
    out.sort(key=lambda jl: (0 if ref and ref in (jl.entry.memo or "").lower() else 1, abs((jl.entry.date - line.date).days)))
    return out


def auto_match(account, user=None):
    n = 0
    for line in BankStatementLine.objects.filter(account=account, status=BankStatementLine.UNMATCHED):
        c = candidates(line)
        if len(c) == 1 or (c and line.reference and line.reference.lower() in (c[0].entry.memo or "").lower()):
            match(line, c[0].pk, user)
            n += 1
    return n


def match(line, journal_line_id, user=None):
    jl = JournalLine.objects.filter(pk=journal_line_id, account=line.account, statement_line__isnull=True).first()
    if jl is None:
        raise BankError("That entry is not available to match.")
    if _book_amount(jl) != line.amount:
        raise BankError("The amounts differ, so these two cannot be matched.")
    line.journal_line, line.status, line.matched_by = jl, BankStatementLine.MATCHED, user
    line.save(update_fields=["journal_line", "status", "matched_by"])


def unmatch(line):
    line.journal_line, line.status, line.matched_by = None, BankStatementLine.UNMATCHED, None
    line.save(update_fields=["journal_line", "status", "matched_by"])


def ignore(line, reason, user=None):
    if not reason:
        raise BankError("Give a short reason for ignoring this line.")
    line.status, line.note, line.matched_by, line.journal_line = BankStatementLine.IGNORED, reason[:200], user, None
    line.save(update_fields=["status", "note", "matched_by", "journal_line"])


def book_balance(account, as_of):
    t = JournalLine.objects.filter(account=account, entry__date__lte=as_of).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (t["d"] or ZERO) - (t["c"] or ZERO)


def summary(account, as_of):
    """What is left to explain: book entries with no statement line (in transit) and statement lines with no entry."""
    unmatched = BankStatementLine.objects.filter(account=account, status=BankStatementLine.UNMATCHED)
    in_books = JournalLine.objects.filter(account=account, statement_line__isnull=True, entry__date__lte=as_of)
    transit = sum((jl.debit for jl in in_books if jl.debit > jl.credit), ZERO)
    outstanding = sum((jl.credit for jl in in_books if jl.credit > jl.debit), ZERO)
    last = BankStatementLine.objects.filter(account=account, balance__isnull=False).order_by("-date", "-id").first()
    return {"book_balance": book_balance(account, as_of), "unmatched_count": unmatched.count(),
            "unmatched_total": unmatched.aggregate(s=Sum("amount"))["s"] or ZERO,
            "deposits_in_transit": transit, "outstanding_payments": outstanding,
            "statement_balance": last.balance if last else None}


def reconcile(account, statement_date, statement_balance, user=None, notes=""):
    s = summary(account, statement_date)
    return BankReconciliation.objects.create(
        account=account, statement_date=statement_date, statement_balance=statement_balance, book_balance=s["book_balance"],
        deposits_in_transit=s["deposits_in_transit"], outstanding_payments=s["outstanding_payments"], notes=notes[:255], reconciled_by=user)
