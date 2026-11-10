"""Finance: posting service, invoices/receipts, payables, expenses, payroll maths, tax obligations, statements."""
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import roles as R
from apps.contacts.models import Client
from apps.finance import payroll, reports, services
from apps.finance.models import (Account, Expense, Invoice, JournalEntry, KraObligation, PayrollLine, PayrollRun,
                                 Receipt)
from apps.finance.services import UnbalancedEntry
from apps.leads import services as lead_services
from apps.people.models import Employee
from apps.sales import services as sales
from apps.suppliers.models import Bill, Supplier


def _client(name="Wanjiru Kamau", phone="0712000001"):
    return Client.objects.create(full_name=name, phone=phone)


def _invoice(client=None, amount=100000, vat=D("16"), currency="KES", issue=True):
    inv = services.create_invoice(client or _client(), lines=[{"description": "Safari", "quantity": D(1), "unit_price": D(amount)}],
                                  currency=currency, fx=D("1") if currency == "KES" else D("130"), vat_rate=vat)
    return services.issue_invoice(inv) if issue else inv


# ------------------------------------------------------------------------------------------------ posting service
@pytest.mark.django_db
def test_entries_must_balance_and_are_idempotent():
    with pytest.raises(UnbalancedEntry):
        services.post(date.today(), "bad", [("bank", 100, 0), ("ar", 0, 99)])
    with pytest.raises(UnbalancedEntry):
        services.post(date.today(), "empty", [])
    e = services.post(date.today(), "ok", [("bank", 100, 0), ("ar", 0, 100)], ("t", 1, "x"))
    again = services.post(date.today(), "ok", [("bank", 100, 0), ("ar", 0, 100)], ("t", 1, "x"))
    assert e.pk == again.pk and JournalEntry.objects.count() == 1 and e.number == "JRN-00001"


@pytest.mark.django_db
def test_chart_is_seeded_with_every_key_the_services_use():
    services.ensure_chart()
    keys = set(Account.objects.values_list("key", flat=True))
    for k in ("bank", "mpesa", "ar", "ap", "vat_output", "vat_input", "deposits_held", "rev_tours", "paye_payable",
              "net_wages_payable", "exp_wages", "prepaid_park_fees", "wht_payable"):
        assert k in keys


# ------------------------------------------------------------------------------------------------ invoices & receipts
@pytest.mark.django_db
def test_invoice_vat_exclusive_and_inclusive():
    inv = _invoice(amount=10000, vat=D("16"), issue=False)
    assert inv.subtotal == D("10000.00") and inv.vat_amount == D("1600.00") and inv.total == D("11600.00")
    incl = services.create_invoice(_client("Inc", "0712000002"), lines=[{"description": "x", "quantity": D(1), "unit_price": D("11600")}],
                                   vat_rate=D("16"), prices_include_vat=True)
    assert incl.total == D("11600.00") and incl.vat_amount == D("1600.00") and incl.subtotal == D("10000.00")


@pytest.mark.django_db
def test_issue_posts_receivable_and_receipts_settle_it():
    inv = _invoice(amount=100000)
    ar = Account.objects.get(key="ar")
    assert services._bal(ar, services._balances()) == D("116000.00")
    with pytest.raises(ValueError):
        services.issue_invoice(inv)                       # already issued
    r1 = services.record_receipt(inv, amount=D("50000"), method="mpesa", reference="SKL1")
    inv.refresh_from_db()
    assert inv.status == Invoice.PART and inv.balance == D("66000.00") and r1.number == "RCT-0001"
    with pytest.raises(ValueError, match="more than"):
        services.record_receipt(inv, amount=D("70000"))
    services.record_receipt(inv, amount=D("66000"), method="bank")
    inv.refresh_from_db()
    assert inv.status == Invoice.PAID and services._bal(ar, services._balances()) == 0
    assert services.balance_sheet(timezone.localdate())["balanced"]


@pytest.mark.django_db
def test_cannot_receive_against_draft_and_void_rules():
    draft = _invoice(issue=False)
    with pytest.raises(ValueError):
        services.record_receipt(draft, amount=D("10"))
    sent = _invoice(client=_client("B", "0712000003"))
    services.void_invoice(sent)
    assert sent.status == Invoice.VOID and services._bal(Account.objects.get(key="ar"), services._balances()) == 0
    paid = _invoice(client=_client("C", "0712000004"))
    services.record_receipt(paid, amount=D("1000"))
    with pytest.raises(ValueError):
        services.void_invoice(paid)


@pytest.mark.django_db
def test_unmatched_mpesa_import_and_auto_match():
    inv = _invoice(amount=10000, vat=D("0"))
    csv_text = (f"date,amount,reference,account\n2026-10-01T10:00:00,4000,QWE111,{inv.number}\n"
                f"2026-10-02T11:00:00,500,QWE222,UNKNOWN\n2026-10-01T10:00:00,4000,QWE111,{inv.number}\nbad,row\n")
    res = services.import_mpesa_csv(csv_text)
    assert res["created"] == 2 and res["duplicates"] == 1 and res["matched"] == 1 and res["invalid"] >= 1
    inv.refresh_from_db()
    assert inv.amount_paid == D("4000.00") and Receipt.objects.filter(invoice__isnull=True).count() == 1
    # unmatched money sits in deposits held, never in receivables
    assert services.balance_sheet(timezone.localdate())["balanced"]


@pytest.mark.django_db
def test_revenue_recognised_only_when_trip_is_completed(agent):
    from apps.catalog.models import TourPackage
    from apps.sales.models import BookingStatus

    pkg = TourPackage.objects.create(title="Mara", days=3, nights=2, currency="KES", price_per_person=D("50000"), status="published")
    e = lead_services.create_enquiry(client_data={"full_name": "Guest", "phone": "0711111111"},
                                     enquiry_data={"source": "call", "package": pkg, "adults": 2}, auto_assign=False)
    lead_services.assign_agent(e, agent, None, "manual")
    q = sales.create_quotation(e, agent.user, currency="KES", items=[{"kind": "other", "description": "T", "quantity": 2, "unit_price": 50000}])
    sales.send_quotation(q, "email", agent.user)
    b = sales.accept_quotation(q, agent.user)
    inv = b.invoices.get()
    assert inv.status == Invoice.SENT and inv.total == b.total_amount
    sales.record_payment(b, agent.user, amount=b.balance)                         # → receipt, confirmed
    inv.refresh_from_db(); b.refresh_from_db()
    assert inv.status == Invoice.PAID and b.status == BookingStatus.CONFIRMED and inv.receipts.count() == 1
    today = timezone.localdate()
    assert services.income_statement(today - timedelta(days=1), today)["revenue"] == 0
    sales.set_booking_status(b, BookingStatus.COMPLETED, agent.user)
    pnl = services.income_statement(today - timedelta(days=1), today)
    assert pnl["revenue"] == inv.total_kes - inv.vat_amount
    assert services.balance_sheet(today)["balanced"]


@pytest.mark.django_db
def test_receivables_aging_buckets():
    old = _invoice(client=_client("Old", "0712000010"), amount=1000, vat=D("0"))
    older = _invoice(client=_client("Older", "0712000011"), amount=2000, vat=D("0"))
    fresh = _invoice(client=_client("Fresh", "0712000012"), amount=4000, vat=D("0"))
    Invoice.objects.filter(pk=old.pk).update(due_date=timezone.localdate() - timedelta(days=45))
    Invoice.objects.filter(pk=older.pk).update(due_date=timezone.localdate() - timedelta(days=120))
    ag = services.receivables_aging()["buckets"]
    assert ag["0-30"] == D("4000.00") and ag["31-60"] == D("1000.00") and ag["90+"] == D("2000.00")


# ------------------------------------------------------------------------------------------------ payables, expenses
@pytest.fixture
def supplier(db):
    return Supplier.objects.create(name="Mara Serena", kind="lodge", payment_terms_days=30)


def _bill(supplier, amount=D("116000"), vat=D("16000"), **kw):
    return Bill.objects.create(supplier=supplier, category=kw.pop("category", "accommodation"), amount=amount, vat_amount=vat,
                               due_date=timezone.localdate() + timedelta(days=10), **kw)


@pytest.mark.django_db
def test_bill_approval_posts_payable_and_input_vat_then_payment(supplier):
    b = _bill(supplier)
    with pytest.raises(ValueError):
        services.pay_bill(b)                                  # not approved yet
    services.approve_bill(b)
    assert services._bal(Account.objects.get(key="ap"), services._balances()) == D("116000.00")
    assert services._bal(Account.objects.get(key="vat_input"), services._balances()) == D("16000.00")
    with pytest.raises(ValueError):
        services.pay_bill(b, amount=D("999999"))
    services.pay_bill(b, amount=D("16000"), method="mpesa")
    services.pay_bill(b, method="bank")
    b.refresh_from_db()
    assert b.status == Bill.PAID and b.balance == 0
    assert services._bal(Account.objects.get(key="ap"), services._balances()) == 0
    assert services.balance_sheet(timezone.localdate())["balanced"]


@pytest.mark.django_db
def test_withholding_tax_on_contractor_bills_and_prepayment_release(supplier):
    supplier.withholding_applies = True
    supplier.save()
    b = _bill(supplier, amount=D("100000"), vat=D("0"), category="professional")
    services.approve_bill(b)
    b.refresh_from_db()
    assert b.wht_amount == D("5000.00") and b.payable_amount == D("95000.00")
    assert services._bal(Account.objects.get(key="wht_payable"), services._balances()) == D("5000.00")
    services.pay_bill(b)
    b.refresh_from_db()
    assert b.status == Bill.PAID and b.balance == 0
    # prepayment sits on the balance sheet until the trip is done
    from apps.catalog.models import TourPackage
    from apps.trips.models import Departure

    pkg = TourPackage.objects.create(title="T", days=2, nights=1, status="published")
    dep = Departure.objects.create(package=pkg, start_date=timezone.localdate(), end_date=timezone.localdate())
    pre = _bill(supplier, amount=D("30000"), vat=D("0"), category="park_fees", prepayment=True, departure=dep)
    services.approve_bill(pre)
    assert services._bal(Account.objects.get(key="prepaid_park_fees"), services._balances()) == D("30000.00")
    services.release_prepayments(dep)
    assert services._bal(Account.objects.get(key="prepaid_park_fees"), services._balances()) == 0
    assert services._bal(Account.objects.get(key="exp_park_fees"), services._balances()) == D("30000.00")


@pytest.mark.django_db
def test_expense_workflow_posts_only_when_paid(make_user):
    u = make_user("fin@example.com", R.FINANCE)
    e = services.record_expense(u, date=timezone.localdate(), category="fuel", payee="Shell", amount=D("5800"), vat_amount=D("800"), method="mpesa")
    assert e.number == "EXP-0001" and e.status == Expense.PENDING and e.has_receipt is False
    with pytest.raises(ValueError):
        services.pay_expense(e)
    services.approve_expense(e, u)
    services.pay_expense(e, u)
    b = services._balances()
    assert services._bal(Account.objects.get(key="exp_fuel"), b) == D("5000.00") and services._bal(Account.objects.get(key="mpesa"), b) == D("-5800.00")
    with pytest.raises(ValueError):
        services.approve_expense(e, u)


# ------------------------------------------------------------------------------------------------ payroll
RATES = None


def _rates():
    global RATES
    from apps.finance.chart import TAX_DEFAULTS

    return {k: (data if data is not None else v) for k, _, v, data, _ in TAX_DEFAULTS}


@pytest.mark.parametrize("gross,nssf,shif,ahl,paye,net", [
    (38000, "2280.00", "1045.00", "570.00", "2614.85", "31490.15"),
    (42000, "2520.00", "1155.00", "630.00", "3691.85", "34003.15"),
    (45000, "2700.00", "1237.50", "675.00", "4499.60", "35887.90"),
    (65000, "3900.00", "1787.50", "975.00", "9884.60", "48452.90"),
    (85000, "4320.00", "2337.50", "1275.00", "15503.60", "61563.90"),
])
def test_payroll_calculator_known_salaries(gross, nssf, shif, ahl, paye, net):
    c = payroll.calculate(D(gross), rates=_rates())
    assert (c["nssf"], c["shif"], c["ahl"], c["paye"], c["net"]) == (D(nssf), D(shif), D(ahl), D(paye), D(net))
    assert c["gross"] - c["nssf"] - c["shif"] - c["ahl"] - c["paye"] == c["net"]


def test_payroll_guide_with_trip_allowances_and_floors():
    c = payroll.calculate(D("20000"), D("12000"), rates=_rates())          # lower basic + trip allowances
    assert c["gross"] == D("32000.00") and c["paye"] > 0
    low = payroll.calculate(D("20000"), rates=_rates())
    assert low["paye"] == 0                                               # relief wipes out tax on a low salary
    assert payroll.calculate(D("1000"), rates=_rates())["shif"] == D("300")   # SHIF minimum


@pytest.mark.django_db
def test_payroll_run_posts_balanced_entry_and_creates_kra_obligations(make_user):
    for name, basic, guide in (("Office Person", 65000, False), ("Safari Guide", 30000, True)):
        Employee.objects.create(full_name=name, basic_salary=D(basic), is_guide=guide)
    period = timezone.localdate().replace(day=1)
    run = services.prepare_payroll(period)
    assert run.lines.count() == 2 and run.status == PayrollRun.PREPARE
    line = run.lines.get(employee__is_guide=True)
    services.recalc_line(line, allowances=D("8000"))
    assert line.gross == D("38000.00") and line.paye == D("2614.85")
    services.advance_payroll(run)
    assert run.status == PayrollRun.REVIEW
    services.advance_payroll(run)
    run.refresh_from_db()
    assert run.status == PayrollRun.APPROVED
    t = run.totals()
    assert services._bal(Account.objects.get(key="paye_payable"), services._balances()) == t["paye"]
    assert services._bal(Account.objects.get(key="net_wages_payable"), services._balances()) == t["net"]
    paye = KraObligation.objects.get(tax="paye", period=period)
    assert paye.amount == t["paye"] and paye.status == KraObligation.READY and paye.due_date.day == 9
    services.pay_payroll(run)
    assert services._bal(Account.objects.get(key="net_wages_payable"), services._balances()) == 0
    with pytest.raises(ValueError):
        services.pay_payroll(run)


# ------------------------------------------------------------------------------------------------ statements
@pytest.mark.django_db
def test_statements_balance_and_cash_flow_reconciles(supplier):
    inv = _invoice(amount=200000)
    services.record_receipt(inv, amount=D("100000"), method="bank")
    b = _bill(supplier, amount=D("58000"), vat=D("8000"), category="transport")
    services.approve_bill(b)
    services.pay_bill(b, method="bank")
    e = services.record_expense(None, date=timezone.localdate(), category="office", payee="Landlord", amount=D("30000"), method="bank", auto_approve=True)
    today = timezone.localdate()
    start = today.replace(day=1)
    bs = services.balance_sheet(today)
    cf = services.cash_flow(start, today)
    assert bs["balanced"] and bs["check"] == 0
    assert cf["check"] == 0 and cf["closing_cash"] == services.cash_position(today)
    pnl = services.income_statement(start, today)
    assert pnl["expenses"] == D("80000.00") and pnl["net_profit"] == -D("80000.00")
    for slug, (name, fn, desc) in reports.REPORTS.items():
        title, headers, rows = fn(start, today)
        assert title and headers is not None


@pytest.mark.django_db
def test_budget_vs_actual_flags_overspend():
    from apps.finance.models import Budget

    today = timezone.localdate()
    Budget.objects.create(period_type="month", year=today.year, index=today.month, category="office", amount=D("20000"), approved=True)
    services.record_expense(None, date=today, category="office", payee="L", amount=D("30000"), method="bank", auto_approve=True)
    rows = services.budget_vs_actual(today.year, "month", today.month)
    assert rows[0]["used"] == 150 and rows[0]["status"] == "Over budget"


def test_amount_in_words():
    from apps.core.money import amount_in_words

    assert amount_in_words(4850) == "Kenya Shillings four thousand, eight hundred and fifty only"
    assert amount_in_words(21.5, "USD") == "US Dollars twenty-one and 50/100 only"


# ------------------------------------------------------------------------------------------------ permissions & screens
@pytest.mark.django_db
def test_finance_access_rules(client, make_user, agent):
    inv = _invoice()
    fin, director = make_user("fin@example.com", R.FINANCE), make_user("dir@example.com", R.DIRECTOR)
    client.force_login(agent.user)
    assert client.get("/finance/").status_code == 403 and client.get("/finance/invoices/").status_code == 403
    client.force_login(director)
    assert client.get("/finance/").status_code == 200 and client.get(inv.get_absolute_url()).status_code == 200
    assert client.post(reverse("finance:invoice_payment", args=[inv.pk]), {"amount": "10", "method": "bank"}).status_code == 403
    assert client.post(reverse("finance:payment_run")).status_code == 403
    client.force_login(fin)
    r = client.post(reverse("finance:invoice_payment", args=[inv.pk]), {"amount": "1000", "method": "bank", "reference": "X1"})
    inv.refresh_from_db()
    assert r.status_code == 302 and inv.amount_paid == D("1000.00")


@pytest.mark.django_db
def test_every_finance_screen_renders(client, make_user, supplier):
    inv = _invoice()
    rc = services.record_receipt(inv, amount=D("500"), method="mpesa", reference="A1")
    b = _bill(supplier)
    e = services.record_expense(None, date=timezone.localdate(), category="fuel", payee="X", amount=D("100"), method="cash")
    Employee.objects.create(full_name="P", basic_salary=D("50000"))
    run = services.prepare_payroll(timezone.localdate())
    client.force_login(make_user("fin@example.com", R.FINANCE))
    urls = ["/finance/", "/finance/?range=year", "/finance/revenue/", "/finance/receivables/", "/finance/invoices/",
            "/finance/invoices/?status=overdue", "/finance/invoices/?export=csv", "/finance/invoices/new/",
            inv.get_absolute_url(), f"/finance/invoices/{inv.pk}/print/", "/finance/receipts/", "/finance/receipts/?unmatched=1",
            rc.get_absolute_url(), f"/finance/receipts/{rc.pk}/print/", "/finance/receipts/import/", "/finance/payables/",
            b.get_absolute_url(), "/finance/expenses/", "/finance/expenses/new/", e.get_absolute_url(), "/finance/kra/",
            "/finance/tax-settings/", "/finance/payroll/", "/finance/payroll/new/", run.get_absolute_url(),
            run.lines.first().get_absolute_url(), "/finance/calendar/", "/finance/statements/", "/finance/statements/?type=balance",
            "/finance/statements/?type=cash", "/finance/budgets/", "/finance/budgets/new/", "/finance/reports/",
            "/suppliers/", f"/suppliers/{supplier.pk}/", "/suppliers/bills/", "/suppliers/bills/new/", "/suppliers/departures/"]
    urls += [f"/finance/reports/{s}/" for s in reports.REPORTS] + ["/finance/reports/income-statement/?export=xlsx"]
    for url in urls:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code)
        if "text/html" in r["Content-Type"]:
            assert b"{{" not in r.content and b"{%" not in r.content, url
