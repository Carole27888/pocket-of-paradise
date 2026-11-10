"""Double-entry core + invoicing, receipts, expenses, tax, payroll, budgets, calendar."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords

from apps.core.models import Sequence, TimeStampedModel

CENT = Decimal("0.01")


def q2(v) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


# ============================================================================================== accounting core
class Account(models.Model):
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = "asset", "liability", "equity", "income", "expense"
    TYPES = [(ASSET, "Asset"), (LIABILITY, "Liability"), (EQUITY, "Equity"), (INCOME, "Income"),
             (EXPENSE, "Expense")]
    code = models.CharField(max_length=8, unique=True)
    key = models.SlugField(max_length=40, unique=True, help_text="Stable name used by the posting service.")
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPES, db_index=True)
    group = models.CharField(max_length=40, blank=True, help_text="Statement section, e.g. Current assets")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def debit_normal(self) -> bool:
        return self.type in (self.ASSET, self.EXPENSE)


class JournalEntry(TimeStampedModel):
    number = models.CharField(max_length=16, unique=True, blank=True)
    date = models.DateField(db_index=True)
    memo = models.CharField(max_length=240)
    source_type = models.CharField(max_length=30, blank=True, db_index=True)
    source_id = models.PositiveIntegerField(null=True, blank=True)
    source_event = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name_plural = "journal entries"
        constraints = [models.UniqueConstraint(fields=["source_type", "source_id", "source_event"],
                                               condition=~models.Q(source_type=""), name="uniq_journal_source")]

    def __str__(self):
        return f"{self.number} · {self.memo}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number("journal", "JRN", 5)
        super().save(*args, **kwargs)

    @property
    def total(self) -> Decimal:
        return sum((l.debit for l in self.lines.all()), Decimal("0"))


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    memo = models.CharField(max_length=160, blank=True)

    class Meta:
        indexes = [models.Index(fields=["account", "entry"])]


# ============================================================================================== invoicing
class Invoice(TimeStampedModel):
    DRAFT, SENT, PART, PAID, VOID = "draft", "sent", "part_paid", "paid", "void"
    STATUSES = [(DRAFT, "Draft"), (SENT, "Sent"), (PART, "Part paid"), (PAID, "Paid"), (VOID, "Void")]

    number = models.CharField(max_length=16, unique=True, blank=True)
    client = models.ForeignKey("contacts.Client", on_delete=models.PROTECT, related_name="invoices")
    booking = models.ForeignKey("sales.Booking", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="invoices")
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    currency = models.CharField(max_length=3, default="KES")
    fx_rate = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    vat_rate = models.DecimalField("VAT %", max_digits=5, decimal_places=2, default=0)
    prices_include_vat = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    etims_cu_number = models.CharField("eTIMS CU invoice no.", max_length=40, blank=True)
    recur_every_months = models.PositiveSmallIntegerField(default=0, help_text="0 = not recurring")
    next_run_date = models.DateField(null=True, blank=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-issue_date", "-pk"]
        indexes = [models.Index(fields=["status", "due_date"])]

    def __str__(self):
        return f"{self.number} · {self.client}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number("invoice", "INV", 4)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("finance:invoice", args=[self.pk])

    @property
    def balance(self) -> Decimal:
        return max(self.total - self.amount_paid, Decimal("0")) if self.status != self.VOID else Decimal("0")

    @property
    def is_open(self) -> bool:
        return self.status in (self.SENT, self.PART)

    @property
    def is_overdue(self) -> bool:
        return self.is_open and self.due_date < timezone.localdate()

    @property
    def days_overdue(self) -> int:
        return max((timezone.localdate() - self.due_date).days, 0) if self.is_open else 0

    @property
    def total_kes(self) -> Decimal:
        return q2(self.total * self.fx_rate)

    @property
    def balance_kes(self) -> Decimal:
        return q2(self.balance * self.fx_rate)

    @property
    def display_status(self) -> str:
        return "Overdue" if self.is_overdue else self.get_status_display()

    def recalculate(self, save=True):
        net = Decimal("0")
        vat = Decimal("0")
        rate = self.vat_rate / 100
        for line in self.lines.all():
            amt = line.amount
            if line.vat_applicable and rate:
                if self.prices_include_vat:
                    base = q2(amt / (1 + rate))
                    vat += amt - base
                    net += base
                else:
                    net += amt
                    vat += q2(amt * rate)
            else:
                net += amt
        self.subtotal, self.vat_amount = q2(net), q2(vat)
        self.total = q2(net + vat)
        if save:
            self.save(update_fields=["subtotal", "vat_amount", "total", "updated_at"])


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vat_applicable = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    @property
    def amount(self) -> Decimal:
        return q2(self.quantity * self.unit_price)


class Receipt(TimeStampedModel):
    METHODS = [("mpesa", "M-PESA"), ("bank", "Bank / RTGS / EFT"), ("cheque", "Cheque"), ("cash", "Cash"),
               ("card", "Card"), ("online", "Online gateway")]
    number = models.CharField(max_length=16, unique=True, blank=True)
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    client = models.ForeignKey("contacts.Client", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="receipts")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    fx_rate = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    method = models.CharField(max_length=8, choices=METHODS, default="mpesa")
    reference = models.CharField("Transaction code", max_length=60, blank=True)
    account_ref = models.CharField("Account / bill ref (as paid)", max_length=60, blank=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    note = models.CharField(max_length=160, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-received_at", "-pk"]

    def __str__(self):
        return f"{self.number} · {self.amount}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number("receipt", "RCT", 4)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("finance:receipt", args=[self.pk])

    @property
    def is_matched(self) -> bool:
        return self.invoice_id is not None

    @property
    def amount_kes(self) -> Decimal:
        return q2(self.amount * self.fx_rate)


# ============================================================================================== expenses
class Expense(TimeStampedModel):
    PENDING, APPROVED, PAID, REJECTED = "pending", "approved", "paid", "rejected"
    STATUSES = [(PENDING, "Pending"), (APPROVED, "Approved"), (PAID, "Paid"), (REJECTED, "Rejected")]
    CATEGORIES = [("fuel", "Vehicle fuel"), ("maintenance", "Vehicle maintenance"), ("park_fees", "Park fees"),
                  ("accommodation", "Accommodation"), ("catering", "Catering"), ("guide_wages", "Guide & driver wages"),
                  ("marketing", "Marketing"), ("office", "Office rent & utilities"), ("professional", "Professional fees"),
                  ("tips", "Tips & incidentals"), ("other", "Other")]
    METHODS = [("mpesa", "M-PESA"), ("bank", "Bank"), ("cash", "Cash / petty cash"), ("card", "Card")]

    number = models.CharField(max_length=16, unique=True, blank=True)
    date = models.DateField(default=timezone.localdate, db_index=True)
    category = models.CharField(max_length=14, choices=CATEGORIES, default="other", db_index=True)
    payee = models.CharField(max_length=120)
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField("Amount (incl. VAT)", max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    method = models.CharField(max_length=6, choices=METHODS, default="mpesa")
    receipt_file = models.FileField(upload_to="expenses/%Y/%m/", blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING, db_index=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    departure = models.ForeignKey("trips.Departure", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="expenses")
    vehicle = models.ForeignKey("fleet.Vehicle", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="expenses")
    recurring_monthly = models.BooleanField(default=False)
    source = models.CharField(max_length=20, blank=True, help_text="e.g. marketing_ad_spend, fuel_log")

    history = HistoricalRecords()

    class Meta:
        ordering = ["-date", "-pk"]

    def __str__(self):
        return f"{self.number} · {self.payee}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next_number("expense", "EXP", 4)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("finance:expense", args=[self.pk])

    @property
    def has_receipt(self) -> bool:
        return bool(self.receipt_file)


# ============================================================================================== tax & payroll
class TaxSetting(models.Model):
    """Every rate, band and due day lives here: editable by Admin, never hard-coded."""

    key = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=100)
    value = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    data = models.JSONField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.label


class KraObligation(TimeStampedModel):
    PAYE, AHL, NSSF, SHIF, VAT, WHT, CORP, TOURISM = "paye", "ahl", "nssf", "shif", "vat", "wht", "corp", "tourism"
    TAXES = [(PAYE, "PAYE"), (AHL, "Affordable Housing Levy"), (NSSF, "NSSF"), (SHIF, "SHIF"), (VAT, "VAT"),
             (WHT, "Withholding tax"), (CORP, "Corporation tax"), (TOURISM, "Tourism / catering levy")]
    DRAFT, READY, FILED, SCHEDULED = "draft", "ready", "filed", "scheduled"
    STATUSES = [(DRAFT, "Draft"), (READY, "Ready to file"), (FILED, "Filed & paid"), (SCHEDULED, "Scheduled")]

    tax = models.CharField(max_length=8, choices=TAXES, db_index=True)
    period = models.DateField(help_text="First day of the month the tax relates to")
    basis = models.CharField(max_length=160, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, db_index=True)
    payment_ref = models.CharField(max_length=60, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "tax"]
        unique_together = [("tax", "period")]

    def __str__(self):
        return f"{self.get_tax_display()} {self.period:%b %Y}"

    @property
    def is_overdue(self) -> bool:
        return self.status != self.FILED and self.due_date < timezone.localdate()


class PayrollRun(TimeStampedModel):
    PREPARE, REVIEW, APPROVED, PAID = "prepare", "review", "approved", "paid"
    STATUSES = [(PREPARE, "Prepare"), (REVIEW, "Review"), (APPROVED, "Approved"), (PAID, "Paid & remitted")]
    period = models.DateField(unique=True, help_text="First day of the pay month")
    status = models.CharField(max_length=10, choices=STATUSES, default=PREPARE, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-period"]

    def __str__(self):
        return f"Payroll {self.period:%B %Y}"

    def get_absolute_url(self):
        return reverse("finance:payroll_run", args=[self.pk])

    def totals(self) -> dict:
        keys = ["gross", "paye", "nssf", "shif", "ahl", "nssf_employer", "ahl_employer", "other_deductions", "net"]
        out = {k: Decimal("0") for k in keys}
        for l in self.lines.all():
            for k in keys:
                out[k] += getattr(l, k)
        out["employees"] = len(self.lines.all())
        return out


class PayrollLine(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey("people.Employee", on_delete=models.PROTECT, related_name="payroll_lines")
    basic = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Trip, per-diem, night-out")
    commission = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    nssf = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    shif = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ahl = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    nssf_employer = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ahl_employer = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    taxable = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paye = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Advances, loans")
    net = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    method = models.CharField(max_length=6, default="bank")

    class Meta:
        ordering = ["employee__full_name"]
        unique_together = [("run", "employee")]

    def get_absolute_url(self):
        return reverse("finance:payslip", args=[self.pk])


# ============================================================================================== budgets & calendar
class Budget(TimeStampedModel):
    MONTH, QUARTER, YEAR = "month", "quarter", "year"
    PERIODS = [(MONTH, "Monthly"), (QUARTER, "Quarterly"), (YEAR, "Annual")]
    period_type = models.CharField(max_length=8, choices=PERIODS, default=MONTH)
    year = models.PositiveSmallIntegerField()
    index = models.PositiveSmallIntegerField(default=1, help_text="Month 1-12 or quarter 1-4 (ignored for annual)")
    category = models.CharField(max_length=30, help_text="Expense category key, or 'revenue'")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-year", "period_type", "index", "category"]
        unique_together = [("period_type", "year", "index", "category")]

    def __str__(self):
        return f"{self.category} {self.year} {self.period_type}"

    @property
    def category_label(self) -> str:
        return self.category.replace("_", " ").title()

    @property
    def period_label(self) -> str:
        return f"{self.year} · " + (f"month {self.index}" if self.period_type == "month"
                                    else f"Q{self.index}" if self.period_type == "quarter" else "annual")

    @property
    def date_range(self):
        import calendar

        if self.period_type == self.MONTH:
            m = self.index
            return (timezone.datetime(self.year, m, 1).date(),
                    timezone.datetime(self.year, m, calendar.monthrange(self.year, m)[1]).date())
        if self.period_type == self.QUARTER:
            m1 = 3 * (self.index - 1) + 1
            return (timezone.datetime(self.year, m1, 1).date(),
                    timezone.datetime(self.year, m1 + 2, calendar.monthrange(self.year, m1 + 2)[1]).date())
        return timezone.datetime(self.year, 1, 1).date(), timezone.datetime(self.year, 12, 31).date()


class FinanceEvent(models.Model):
    title = models.CharField(max_length=140)
    date = models.DateField(db_index=True)
    category = models.CharField(max_length=20, default="other")
    notes = models.CharField(max_length=200, blank=True)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["date", "title"]

    def __str__(self):
        return self.title


# ---- computed columns used by the list screens
from decimal import Decimal  # noqa: F401
Receipt.match_label = property(lambda self: "Matched" if self.is_matched else "Unmatched")
Expense.receipt_label = property(lambda self: "Attached" if self.has_receipt else "Missing")
PayrollRun.period_label = property(lambda self: self.period.strftime("%B %Y"))
PayrollRun.staff_count = property(lambda self: len(self.lines.all()))
PayrollRun.gross_total = property(lambda self: sum((l.gross for l in self.lines.all()), Decimal("0")))
PayrollRun.net_total = property(lambda self: sum((l.net for l in self.lines.all()), Decimal("0")))


# ============================================================================================== bank reconciliation
class BankStatementLine(models.Model):
    """One line of an imported bank statement, matched to the books (or explained) during reconciliation."""

    UNMATCHED, MATCHED, IGNORED = "unmatched", "matched", "ignored"
    STATUSES = [(UNMATCHED, "Not matched"), (MATCHED, "Matched"), (IGNORED, "Ignored")]

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="statement_lines")
    date = models.DateField(db_index=True)
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, help_text="Money in is positive, money out negative")
    balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=UNMATCHED, db_index=True)
    journal_line = models.OneToOneField(JournalLine, null=True, blank=True, on_delete=models.SET_NULL, related_name="statement_line")
    note = models.CharField(max_length=200, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True, help_text="Detects the same line imported twice")
    batch = models.CharField(max_length=40, blank=True)
    matched_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [models.UniqueConstraint(fields=["account", "fingerprint"], name="unique_statement_line")]

    def __str__(self):
        return f"{self.date} {self.amount} {self.description[:40]}"


class BankReconciliation(models.Model):
    """Statement balance + deposits in transit - outstanding payments must equal the balance in the books."""

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="bank_reconciliations")
    statement_date = models.DateField()
    statement_balance = models.DecimalField(max_digits=16, decimal_places=2)
    book_balance = models.DecimalField(max_digits=16, decimal_places=2)
    deposits_in_transit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    outstanding_payments = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)
    reconciled_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-statement_date", "-id"]

    @property
    def adjusted_bank(self):
        return self.statement_balance + self.deposits_in_transit - self.outstanding_payments

    @property
    def difference(self):
        return self.adjusted_bank - self.book_balance

    @property
    def balanced(self) -> bool:
        return self.difference == 0
