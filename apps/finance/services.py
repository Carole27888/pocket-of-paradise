"""Finance business logic: posting service, documents, payroll, tax obligations and statements."""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import payroll as payroll_calc
from .chart import CATEGORY_ACCOUNT, CHART, METHOD_ACCOUNT, TAX_DEFAULTS
from .models import (Account, Budget, Expense, Invoice, InvoiceLine, JournalEntry, JournalLine, KraObligation,
                     PayrollLine, PayrollRun, Receipt, TaxSetting, q2)

D = Decimal


class UnbalancedEntry(ValueError):
    pass


# ============================================================================================== setup
def ensure_chart():
    if Account.objects.count() >= len(CHART):
        return
    for code, key, name, typ, group in CHART:
        Account.objects.get_or_create(key=key, defaults={"code": code, "name": name, "type": typ, "group": group})


def ensure_tax_settings():
    if TaxSetting.objects.count() >= len(TAX_DEFAULTS):
        return
    for key, label, value, data, note in TAX_DEFAULTS:
        TaxSetting.objects.get_or_create(key=key, defaults={"label": label, "value": value, "data": data, "note": note})


def account(key: str) -> Account:
    ensure_chart()
    return Account.objects.get(key=key)


def rate(key: str) -> D:
    ensure_tax_settings()
    t = TaxSetting.objects.get(key=key)
    return t.value if t.value is not None else D("0")


# ============================================================================================== posting service
@transaction.atomic
def post(date_, memo: str, lines: list[tuple], source: tuple | None = None, user=None) -> JournalEntry:
    """Create a balanced journal entry. `lines` = [(account_key, debit, credit[, memo])]. Idempotent per source."""
    ensure_chart()
    src_type, src_id, src_event = source or ("", None, "")
    if source:
        existing = JournalEntry.objects.filter(source_type=src_type, source_id=src_id, source_event=src_event).first()
        if existing:
            return existing
    clean = [(k, q2(d or 0), q2(c or 0), *(rest[:1] if rest else ("",))) for k, d, c, *rest in lines]
    clean = [l for l in clean if l[1] or l[2]]
    debit, credit = sum((l[1] for l in clean), D("0")), sum((l[2] for l in clean), D("0"))
    if not clean or debit != credit:
        raise UnbalancedEntry(f"Entry does not balance: debit {debit} vs credit {credit} ({memo})")
    if any(l[1] < 0 or l[2] < 0 or (l[1] and l[2]) for l in clean):
        raise UnbalancedEntry("Each line must carry a single positive debit or credit.")
    entry = JournalEntry.objects.create(date=date_, memo=memo[:240], source_type=src_type, source_id=src_id,
                                        source_event=src_event, created_by=user)
    accounts = {a.key: a for a in Account.objects.filter(key__in={l[0] for l in clean})}
    JournalLine.objects.bulk_create([JournalLine(entry=entry, account=accounts[k], debit=d, credit=c, memo=m)
                                     for k, d, c, m in clean])
    return entry


# ============================================================================================== invoices & receipts
def _split_kes(total, vat, fx):
    total_k = q2(D(total) * fx)
    vat_k = q2(D(vat) * fx)
    return total_k, vat_k, total_k - vat_k


@transaction.atomic
def create_invoice(client, *, lines, currency="KES", fx=None, vat_rate=D("0"), prices_include_vat=False,
                   issue_date=None, due_date=None, booking=None, notes="", user=None) -> Invoice:
    from apps.sales.services import fx_rate

    issue_date = issue_date or timezone.localdate()
    inv = Invoice.objects.create(client=client, booking=booking, issue_date=issue_date,
                                 due_date=due_date or issue_date + timedelta(days=14), currency=currency,
                                 fx_rate=fx if fx is not None else fx_rate(currency), vat_rate=vat_rate,
                                 prices_include_vat=prices_include_vat, notes=notes, created_by=user)
    for pos, ln in enumerate(lines):
        InvoiceLine.objects.create(invoice=inv, position=pos, **ln)
    inv.recalculate()
    return inv


@transaction.atomic
def issue_invoice(inv: Invoice, user=None) -> Invoice:
    if inv.status != Invoice.DRAFT:
        raise ValueError("Only a draft invoice can be issued.")
    if not inv.lines.exists() or inv.total <= 0:
        raise ValueError("Add at least one line with an amount before issuing.")
    inv.recalculate()
    total_k, vat_k, net_k = _split_kes(inv.total, inv.vat_amount, inv.fx_rate)
    post(inv.issue_date, f"Invoice {inv.number} · {inv.client}",
         [("ar", total_k, 0), ("deposits_held", 0, net_k), ("vat_output", 0, vat_k)], ("invoice", inv.pk, "issue"), user)
    inv.status = Invoice.SENT
    inv.save(update_fields=["status", "updated_at"])
    return inv


@transaction.atomic
def void_invoice(inv: Invoice, user=None) -> Invoice:
    if inv.status in (Invoice.VOID, Invoice.PAID, Invoice.PART) or inv.receipts.exists():
        raise ValueError("Only an unpaid invoice can be voided.")
    if inv.status == Invoice.SENT:
        total_k, vat_k, net_k = _split_kes(inv.total, inv.vat_amount, inv.fx_rate)
        post(timezone.localdate(), f"Void invoice {inv.number}",
             [("ar", 0, total_k), ("deposits_held", net_k, 0), ("vat_output", vat_k, 0)], ("invoice", inv.pk, "void"), user)
    inv.status = Invoice.VOID
    inv.save(update_fields=["status", "updated_at"])
    return inv


def invoice_for_booking(booking, user=None, issue_date=None, due_date=None) -> Invoice:
    """Issue the invoice for an accepted quotation (one per booking)."""
    existing = booking.invoices.exclude(status=Invoice.VOID).first()
    if existing:
        return existing
    q = booking.quotation
    lines = ([{"description": i.description, "quantity": i.quantity, "unit_price": i.unit_price,
               "vat_applicable": q.vat_rate > 0} for i in q.items.all()] if q else [])
    if q and q.discount:
        lines.append({"description": "Discount", "quantity": D(1), "unit_price": -q.discount, "vat_applicable": q.vat_rate > 0})
    if not lines:
        lines = [{"description": booking.title, "quantity": D(1), "unit_price": booking.total_amount, "vat_applicable": False}]
    due = due_date or (booking.travel_start - timedelta(days=30) if booking.travel_start else None)
    if due is None or (due_date is None and due < timezone.localdate() + timedelta(days=7)):
        due = timezone.localdate() + timedelta(days=14)
    inv = create_invoice(booking.client, lines=lines, currency=booking.currency, fx=booking.fx_rate,
                         vat_rate=q.vat_rate if q else D("0"), due_date=due, issue_date=issue_date, booking=booking,
                         notes=f"Booking {booking.reference}", user=user)
    if inv.total != booking.total_amount:      # keep the booking total authoritative
        inv.total = booking.total_amount
        inv.subtotal = inv.total - inv.vat_amount
        inv.save(update_fields=["total", "subtotal"])
    return issue_invoice(inv, user)


def _refresh_invoice_status(inv: Invoice):
    paid = inv.receipts.aggregate(t=Sum("amount"))["t"] or D("0")
    inv.amount_paid = paid
    if inv.status in (Invoice.SENT, Invoice.PART, Invoice.PAID):
        inv.status = Invoice.PAID if paid >= inv.total else (Invoice.PART if paid > 0 else Invoice.SENT)
    inv.save(update_fields=["amount_paid", "status", "updated_at"])


@transaction.atomic
def record_receipt(invoice: Invoice | None, *, amount, method="mpesa", reference="", received_at=None, note="",
                   account_ref="", client=None, currency=None, user=None) -> Receipt:
    amount = q2(amount)
    if amount <= 0:
        raise ValueError("Enter an amount above zero.")
    if invoice is not None:
        if invoice.status in (Invoice.DRAFT, Invoice.VOID):
            raise ValueError("Issue the invoice before taking payment against it.")
        if amount > invoice.balance:
            raise ValueError(f"That is more than the balance ({invoice.currency} {invoice.balance:,.2f}).")
    fx = invoice.fx_rate if invoice else D("1")
    rcpt = Receipt.objects.create(invoice=invoice, client=invoice.client if invoice else client, amount=amount,
                                  currency=invoice.currency if invoice else (currency or "KES"), fx_rate=fx,
                                  method=method, reference=reference, account_ref=account_ref, note=note,
                                  received_at=received_at or timezone.now(), created_by=user)
    amt_k = q2(amount * fx)
    when = timezone.localtime(rcpt.received_at).date()
    credit = "ar" if invoice else "deposits_held"
    post(when, f"Receipt {rcpt.number}" + (f" · {invoice.number}" if invoice else " (unallocated)"),
         [(METHOD_ACCOUNT[method], amt_k, 0), (credit, 0, amt_k)], ("receipt", rcpt.pk, "receive"), user)
    if invoice:
        _refresh_invoice_status(invoice)
    return rcpt


@transaction.atomic
def match_receipt(rcpt: Receipt, invoice: Invoice, user=None) -> Receipt:
    if rcpt.invoice_id:
        raise ValueError("Receipt is already matched.")
    if invoice.status in (Invoice.DRAFT, Invoice.VOID):
        raise ValueError("That invoice is not open.")
    if rcpt.amount > invoice.balance:
        raise ValueError(f"The receipt is more than the invoice balance ({invoice.balance:,.2f}).")
    amt_k = q2(rcpt.amount * invoice.fx_rate)
    rcpt.invoice, rcpt.client, rcpt.currency, rcpt.fx_rate = invoice, invoice.client, invoice.currency, invoice.fx_rate
    rcpt.save()
    post(timezone.localdate(), f"Matched {rcpt.number} to {invoice.number}",
         [("deposits_held", amt_k, 0), ("ar", 0, amt_k)], ("receipt", rcpt.pk, "match"), user)
    _refresh_invoice_status(invoice)
    return rcpt


def auto_match_receipts(user=None) -> int:
    n = 0
    for r in Receipt.objects.filter(invoice__isnull=True).exclude(account_ref=""):
        inv = Invoice.objects.filter(number__iexact=r.account_ref.strip(), status__in=[Invoice.SENT, Invoice.PART]).first()
        if inv:
            try:
                match_receipt(r, inv, user)
                n += 1
            except ValueError:
                pass
    return n


def import_mpesa_csv(text: str, user=None) -> dict:
    """CSV with header: date,amount,reference,account[,phone]. Records receipts and auto-matches by account ref."""
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(text)))
    created = dup = bad = 0
    for row in rows:
        try:
            ref = (row.get("reference") or "").strip()
            amount = D((row.get("amount") or "0").replace(",", ""))
            if ref and Receipt.objects.filter(reference=ref).exists():
                dup += 1
                continue
            when = timezone.make_aware(timezone.datetime.fromisoformat((row.get("date") or "").strip()))
            record_receipt(None, amount=amount, method="mpesa", reference=ref, received_at=when,
                           account_ref=(row.get("account") or "").strip(), user=user)
            created += 1
        except Exception:
            bad += 1
    matched = auto_match_receipts(user)
    return {"created": created, "duplicates": dup, "invalid": bad, "matched": matched}


@transaction.atomic
def recognise_booking_revenue(booking, user=None, on_date=None):
    """Booking completed: the trip has been delivered, so deposits held become tour revenue."""
    for inv in booking.invoices.filter(status__in=[Invoice.SENT, Invoice.PART, Invoice.PAID]):
        _, _, net_k = _split_kes(inv.total, inv.vat_amount, inv.fx_rate)
        post(on_date or timezone.localdate(), f"Revenue recognised · {inv.number}",
             [("deposits_held", net_k, 0), ("rev_tours", 0, net_k)], ("invoice", inv.pk, "revenue"), user)


# ============================================================================================== supplier bills
@transaction.atomic
def approve_bill(bill, user=None):
    from apps.suppliers.models import Bill

    if bill.status != Bill.AWAITING:
        raise ValueError("Only a bill awaiting approval can be approved.")
    if bill.wht_amount == 0 and bill.supplier.withholding_applies:
        bill.wht_amount = q2((bill.amount - bill.vat_amount) * rate("wht_rate") / 100)
    total_k, vat_k, net_k = _split_kes(bill.amount, bill.vat_amount, bill.fx_rate)
    wht_k = q2(bill.wht_amount * bill.fx_rate)
    if bill.prepayment:
        debit = "prepaid_park_fees" if bill.category == "park_fees" else "prepayments"
    else:
        debit = CATEGORY_ACCOUNT.get(bill.category, "exp_other")
    post(bill.bill_date, f"Bill {bill.number} · {bill.supplier}",
         [(debit, net_k, 0), ("vat_input", vat_k, 0), ("ap", 0, total_k - wht_k), ("wht_payable", 0, wht_k)],
         ("bill", bill.pk, "approve"), user)
    bill.status, bill.approved_by, bill.approved_at = Bill.APPROVED, user, timezone.now()
    bill.save()
    return bill


@transaction.atomic
def pay_bill(bill, user=None, *, amount=None, method="bank", reference="", paid_at=None):
    from apps.suppliers.models import Bill, BillPayment

    if bill.status not in (Bill.APPROVED, Bill.SCHEDULED):
        raise ValueError("Approve the bill before paying it.")
    amount = q2(amount if amount is not None else bill.balance)
    if amount <= 0 or amount > bill.balance:
        raise ValueError(f"Enter an amount up to the balance ({bill.balance:,.2f}).")
    pay = BillPayment.objects.create(bill=bill, amount=amount, method=method, reference=reference,
                                     paid_at=paid_at or timezone.now(), recorded_by=user)
    amt_k = q2(amount * bill.fx_rate)
    post(timezone.localtime(pay.paid_at).date(), f"Payment {bill.number}",
         [("ap", amt_k, 0), (METHOD_ACCOUNT[method], 0, amt_k)], ("bill_payment", pay.pk, "pay"), user)
    bill.__dict__.pop("_paid", None)
    getattr(bill, "_prefetched_objects_cache", {}).pop("payments", None)
    if bill.balance <= 0:
        bill.status, bill.paid_at = Bill.PAID, timezone.now()
        bill.save(update_fields=["status", "paid_at", "updated_at"])
    return pay


def release_prepayments(departure, user=None):
    """Trip done: move prepaid supplier costs for the departure into expense."""
    from apps.suppliers.models import Bill

    for b in departure.bills.filter(prepayment=True, status__in=[Bill.APPROVED, Bill.SCHEDULED, Bill.PAID]):
        net_k = _split_kes(b.amount, b.vat_amount, b.fx_rate)[2]
        src = "prepaid_park_fees" if b.category == "park_fees" else "prepayments"
        post(timezone.localdate(), f"Release prepayment {b.number}",
             [(CATEGORY_ACCOUNT.get(b.category, "exp_other"), net_k, 0), (src, 0, net_k)],
             ("bill", b.pk, "release"), user)


# ============================================================================================== expenses
@transaction.atomic
def approve_expense(exp: Expense, user=None, approve=True):
    if exp.status != Expense.PENDING:
        raise ValueError("Only a pending expense can be reviewed.")
    exp.status = Expense.APPROVED if approve else Expense.REJECTED
    exp.approved_by = user
    exp.save(update_fields=["status", "approved_by", "updated_at"])
    return exp


@transaction.atomic
def pay_expense(exp: Expense, user=None):
    if exp.status != Expense.APPROVED:
        raise ValueError("Approve the expense before paying it.")
    net = exp.amount - exp.vat_amount
    post(exp.date, f"Expense {exp.number} · {exp.payee}",
         [(CATEGORY_ACCOUNT.get(exp.category, "exp_other"), net, 0), ("vat_input", exp.vat_amount, 0),
          (METHOD_ACCOUNT[exp.method], 0, exp.amount)], ("expense", exp.pk, "pay"), user)
    exp.status = Expense.PAID
    exp.save(update_fields=["status", "updated_at"])
    return exp


def record_expense(user=None, *, auto_approve=False, **fields) -> Expense:
    exp = Expense.objects.create(submitted_by=user, created_by=user, **fields)
    if auto_approve:
        approve_expense(exp, user)
        pay_expense(exp, user)
    return exp


# ============================================================================================== payroll
@transaction.atomic
def prepare_payroll(period: date, user=None) -> PayrollRun:
    from apps.people.models import Employee
    from apps.sales.services import sum_kes
    from apps.sales.models import Booking, BookingStatus

    period = period.replace(day=1)
    run, created = PayrollRun.objects.get_or_create(period=period, defaults={"created_by": user})
    if run.status != PayrollRun.PREPARE and not created:
        raise ValueError("This run has moved past preparation.")
    rates = payroll_calc.load_rates()
    last = period.replace(day=calendar.monthrange(period.year, period.month)[1])
    for emp in Employee.objects.filter(is_active=True, basic_salary__gt=0):
        commission = D("0")
        if emp.is_sales_agent:
            won = Booking.objects.exclude(status=BookingStatus.CANCELLED).filter(
                agent=emp, booked_at__date__gte=period, booked_at__date__lte=last)
            commission = q2(sum_kes(won) * rates["sales_commission_rate"] / 100)
        existing = PayrollLine.objects.filter(run=run, employee=emp).first()
        extra = (existing.allowances, existing.other_deductions) if existing else (D("0"), D("0"))
        calc = payroll_calc.calculate(emp.basic_salary, extra[0], commission, extra[1], rates)
        PayrollLine.objects.update_or_create(run=run, employee=emp,
                                             defaults={**calc, "method": "mpesa" if emp.payout_method == "mpesa" else "bank"})
    return run


def recalc_line(line: PayrollLine, allowances=None, other_deductions=None):
    calc = payroll_calc.calculate(line.basic, line.allowances if allowances is None else allowances, line.commission,
                                  line.other_deductions if other_deductions is None else other_deductions)
    for k, v in calc.items():
        setattr(line, k, v)
    line.save()
    return line


def advance_payroll(run: PayrollRun, user=None) -> PayrollRun:
    flow = {PayrollRun.PREPARE: PayrollRun.REVIEW, PayrollRun.REVIEW: PayrollRun.APPROVED}
    if run.status not in flow:
        raise ValueError("Nothing further to advance.")
    if not run.lines.exists():
        raise ValueError("The run has no employees.")
    if flow[run.status] == PayrollRun.APPROVED:
        return approve_payroll(run, user)
    run.status = flow[run.status]
    run.save(update_fields=["status", "updated_at"])
    return run


@transaction.atomic
def approve_payroll(run: PayrollRun, user=None) -> PayrollRun:
    if run.status not in (PayrollRun.REVIEW, PayrollRun.PREPARE):
        raise ValueError("Payroll is already approved.")
    wages = guide = comm = paye = ahl = nssf = shif = net = other = D("0")
    for l in run.lines.select_related("employee"):
        base = l.basic + l.allowances + l.nssf_employer + l.ahl_employer
        if l.employee.is_guide or l.employee.is_driver:
            guide += base
        else:
            wages += base
        comm += l.commission
        paye += l.paye
        ahl += l.ahl + l.ahl_employer
        nssf += l.nssf + l.nssf_employer
        shif += l.shif
        net += l.net
        other += l.other_deductions
    last = run.period.replace(day=calendar.monthrange(run.period.year, run.period.month)[1])
    post(last, f"Payroll {run.period:%B %Y}",
         [("exp_wages", wages, 0), ("exp_guide_wages", guide, 0), ("exp_commission", comm, 0),
          ("paye_payable", 0, paye), ("ahl_payable", 0, ahl), ("nssf_payable", 0, nssf), ("shif_payable", 0, shif),
          ("net_wages_payable", 0, net), ("prepayments", 0, other)], ("payroll", run.pk, "approve"), user)
    run.status, run.approved_by, run.approved_at = PayrollRun.APPROVED, user, timezone.now()
    run.save()
    refresh_obligations(run.period)
    return run


@transaction.atomic
def pay_payroll(run: PayrollRun, user=None, method="bank") -> PayrollRun:
    if run.status != PayrollRun.APPROVED:
        raise ValueError("Approve the payroll before paying it.")
    net = run.totals()["net"]
    post(timezone.localdate(), f"Salaries paid {run.period:%B %Y}",
         [("net_wages_payable", net, 0), (METHOD_ACCOUNT[method], 0, net)], ("payroll", run.pk, "pay"), user)
    run.status, run.paid_at = PayrollRun.PAID, timezone.now()
    run.save()
    return run


# ============================================================================================== KRA obligations
def _due(period: date, key: str) -> date:
    nxt = (period.replace(day=28) + timedelta(days=4)).replace(day=1)
    day = int(rate(key))
    return nxt.replace(day=min(day, calendar.monthrange(nxt.year, nxt.month)[1]))


def _set_obligation(tax, period, amount, basis, due, status=None):
    ob, _ = KraObligation.objects.get_or_create(tax=tax, period=period, defaults={"due_date": due})
    if ob.status == KraObligation.FILED:
        return ob
    ob.amount, ob.basis, ob.due_date = q2(amount), basis, due
    ob.status = status or (KraObligation.READY if amount > 0 else KraObligation.DRAFT)
    ob.save()
    return ob


def refresh_obligations(period: date):
    """Recompute every obligation for the month from payroll, invoices and bills."""
    ensure_tax_settings()
    period = period.replace(day=1)
    end = period.replace(day=calendar.monthrange(period.year, period.month)[1])
    run = PayrollRun.objects.filter(period=period, status__in=[PayrollRun.APPROVED, PayrollRun.PAID]).first()
    if run:
        t = run.totals()
        due = _due(period, "paye_due_day")
        _set_obligation("paye", period, t["paye"], f"PAYE on {t['employees']} employees", due)
        _set_obligation("ahl", period, t["ahl"] + t["ahl_employer"], "Housing levy 1.5% + 1.5%", due)
        _set_obligation("nssf", period, t["nssf"] + t["nssf_employer"], "NSSF employee + employer", due)
        _set_obligation("shif", period, t["shif"], "SHIF 2.75% of gross", due)
    vat = account("vat_output")
    inp = account("vat_input")
    sums = lambda acct, f: JournalLine.objects.filter(account=acct, entry__date__range=(period, end)).aggregate(t=Sum(f))["t"] or D("0")  # noqa: E731
    out_vat = sums(vat, "credit") - sums(vat, "debit")
    in_vat = sums(inp, "debit") - sums(inp, "credit")
    _set_obligation("vat", period, max(out_vat - in_vat, D("0")),
                    f"Output VAT {out_vat:,.0f} less input VAT {in_vat:,.0f}", _due(period, "vat_due_day"))
    wht = account("wht_payable")
    _set_obligation("wht", period, sums(wht, "credit") - sums(wht, "debit"), "Withheld on supplier bills",
                    _due(period, "vat_due_day"))
    if period.month in (3, 5, 8, 11):   # instalment months 4, 6, 9, 12 fall in the following month
        pass
    for n_month in (4, 6, 9, 12):
        if period.month == n_month:
            ytd = income_statement(date(period.year, 1, 1), end)["net_profit"]
            months = period.month
            est_year = max(ytd, D("0")) / months * 12
            amt = q2(est_year * rate("corp_tax_rate") / 100 / 4)
            due = date(period.year, n_month, 20)
            _set_obligation("corp", period, amt, f"25% of estimated annual tax on profit to date", due)


# ============================================================================================== statements
def _balances(start=None, end=None):
    """{account_key: (debit, credit)} within [start, end]."""
    ensure_chart()
    qs = JournalLine.objects.all()
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    out = defaultdict(lambda: (D("0"), D("0")))
    for r in qs.values("account__key").annotate(d=Sum("debit"), c=Sum("credit")):
        out[r["account__key"]] = (r["d"] or D("0"), r["c"] or D("0"))
    return out


def _bal(acct: Account, b) -> D:
    d, c = b[acct.key]
    return d - c if acct.debit_normal else c - d


def income_statement(start: date, end: date) -> dict:
    b = _balances(start, end)
    sections = {}
    for a in Account.objects.filter(type__in=[Account.INCOME, Account.EXPENSE]):
        v = _bal(a, b)
        if v:
            sections.setdefault(a.group, []).append({"account": a, "amount": v})
    revenue = sum((r["amount"] for r in sections.get("Revenue", [])), D("0"))
    cogs = sum((r["amount"] for r in sections.get("Cost of sales", [])), D("0"))
    opex = sum((r["amount"] for r in sections.get("Operating expenses", [])), D("0"))
    tax = sum((r["amount"] for r in sections.get("Tax", [])), D("0"))
    return {"sections": sections, "revenue": revenue, "cost_of_sales": cogs, "gross_profit": revenue - cogs,
            "operating_expenses": opex, "profit_before_tax": revenue - cogs - opex, "tax": tax,
            "net_profit": revenue - cogs - opex - tax, "expenses": cogs + opex}


def balance_sheet(as_of: date) -> dict:
    b = _balances(None, as_of)
    groups = {"asset": {}, "liability": {}, "equity": {}}
    for a in Account.objects.filter(type__in=["asset", "liability", "equity"]):
        v = _bal(a, b)
        if v:
            groups[a.type].setdefault(a.group, []).append({"account": a, "amount": v})
    earnings = D("0")
    for a in Account.objects.filter(type__in=[Account.INCOME, Account.EXPENSE]):
        v = _bal(a, b)
        earnings += v if a.type == Account.INCOME else -v
    total = lambda t: sum((r["amount"] for rows in groups[t].values() for r in rows), D("0"))  # noqa: E731
    assets, liabilities, equity = total("asset"), total("liability"), total("equity") + earnings
    return {"groups": groups, "current_earnings": earnings, "assets": assets, "liabilities": liabilities,
            "equity": equity, "check": assets - liabilities - equity, "balanced": assets == liabilities + equity}


def cash_flow(start: date, end: date) -> dict:
    """Indirect method: profit adjusted for working-capital movements, plus investing and financing."""
    pnl = income_statement(start, end)
    opening, closing = _balances(None, start - timedelta(days=1)), _balances(None, end)

    def delta(key, asset=True):
        a = account(key)
        return _bal(a, closing) - _bal(a, opening)

    wc = [("Receivables", -delta("ar")), ("Prepayments & prepaid park fees", -(delta("prepayments") + delta("prepaid_park_fees"))),
          ("Input VAT", -delta("vat_input")), ("Payables", delta("ap")), ("Customer deposits held", delta("deposits_held")),
          ("VAT, PAYE & other taxes payable", sum((delta(k) for k in ("vat_output", "paye_payable", "ahl_payable", "nssf_payable", "shif_payable", "wht_payable", "tax_payable")), D("0"))),
          ("Wages & accruals payable", delta("net_wages_payable") + delta("accrued"))]
    dep = pnl["sections"].get("Operating expenses", [])
    depreciation = sum((r["amount"] for r in dep if r["account"].key == "exp_depreciation"), D("0"))
    operating = pnl["net_profit"] + depreciation + sum((v for _, v in wc), D("0"))
    investing = -(delta("fixed_assets") + delta("accum_depreciation")) - depreciation
    financing = delta("bank_loan") + delta("share_capital")
    net_change = operating + investing + financing
    cash_keys = ("bank", "mpesa", "cash")
    actual = sum((_bal(account(k), closing) - _bal(account(k), opening) for k in cash_keys), D("0"))
    return {"net_profit": pnl["net_profit"], "depreciation": depreciation, "working_capital": wc, "operating": operating,
            "investing": investing, "financing": financing, "net_change": net_change,
            "opening_cash": sum((_bal(account(k), opening) for k in cash_keys), D("0")),
            "closing_cash": sum((_bal(account(k), closing) for k in cash_keys), D("0")),
            "check": net_change - actual}


def cash_position(as_of=None) -> D:
    b = _balances(None, as_of)
    return sum((_bal(account(k), b) for k in ("bank", "mpesa", "cash")), D("0"))


# ============================================================================================== aging & revenue helpers
BUCKETS = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10 ** 6)]


def _bucket(days: int) -> str:
    for label, lo, hi in BUCKETS:
        if lo <= days <= hi:
            return label
    return "90+"


def receivables_aging() -> dict:
    out = {label: D("0") for label, _, _ in BUCKETS}
    rows = []
    for inv in Invoice.objects.filter(status__in=[Invoice.SENT, Invoice.PART]).select_related("client"):
        bal = inv.balance_kes
        out[_bucket(inv.days_overdue)] += bal
        rows.append(inv)
    return {"buckets": out, "total": sum(out.values(), D("0")), "invoices": rows}


def payables_aging() -> dict:
    from apps.suppliers.models import Bill

    out = {label: D("0") for label, _, _ in BUCKETS}
    today = timezone.localdate()
    bills = Bill.objects.exclude(status__in=[Bill.PAID, Bill.VOID]).select_related("supplier")
    for b in bills:
        out[_bucket(max((today - b.due_date).days, 0))] += q2(b.balance * b.fx_rate)
    return {"buckets": out, "total": sum(out.values(), D("0"))}


def month_series(months=6):
    """[(label, income, expenses)] for the last N months from the journal."""
    today = timezone.localdate()
    cur = today.replace(day=1)
    spans = []
    for _ in range(months):
        spans.append(cur)
        cur = (cur - timedelta(days=1)).replace(day=1)
    out = []
    for first in reversed(spans):
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        s = income_statement(first, last)
        out.append((first.strftime("%b"), s["revenue"], s["expenses"] + s["tax"]))
    return out


# ============================================================================================== budgets
def budget_vs_actual(year: int, period_type="month", index=1):
    rows = []
    for bud in Budget.objects.filter(year=year, period_type=period_type, index=index if period_type != "year" else 1):
        start, end = bud.date_range
        is_rev = bud.category == "revenue"
        if is_rev:
            actual = income_statement(start, end)["revenue"]
        else:
            key = CATEGORY_ACCOUNT.get(bud.category, "exp_other")
            actual = _bal(account(key), _balances(start, end))
        used = round(100 * actual / bud.amount) if bud.amount else 0
        today = timezone.localdate()
        expected = None
        if period_type == "year" and start <= today <= end:
            expected = round(100 * ((today - start).days + 1) / ((end - start).days + 1))
        if is_rev:
            status = "On track" if used >= (expected or 100) * 0.9 else "Below target"
        else:
            status = "Over budget" if used > 100 else ("On track" if used >= 60 else "Under budget")
        rows.append({"budget": bud, "actual": actual, "used": used, "remaining": bud.amount - actual,
                     "status": status, "expected": expected, "revenue": is_rev})
    return rows
