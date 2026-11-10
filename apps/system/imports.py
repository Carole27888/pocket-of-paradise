"""Bulk import: one importer class per data type, sharing the checked → imported → undone state machine."""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.phone import normalise_phone

from .models import ImportBatch
from .services import audit

MAX_ROWS = 5000
YES = {"yes", "y", "true", "1", "x"}


def _norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def read_table(filename: str, raw: bytes) -> list[dict]:
    """CSV or XLSX → list of dicts keyed by normalised header. Raises ValueError for unreadable files."""
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("The file is larger than 5 MB.")
    name = filename.lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            ws = load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
        except Exception as exc:
            raise ValueError("That doesn't look like a valid Excel file.") from exc
        rows = list(ws.iter_rows(values_only=True))
    elif name.endswith((".csv", ".txt")):
        text = raw.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))
    else:
        raise ValueError("Upload a .csv or .xlsx file.")
    rows = [r for r in rows if any(c not in (None, "") for c in r)]
    if len(rows) < 2:
        raise ValueError("The file needs a header row and at least one data row.")
    if len(rows) - 1 > MAX_ROWS:
        raise ValueError(f"Too many rows ({len(rows) - 1}). Import at most {MAX_ROWS} at a time.")
    headers = [_norm_header(h) for h in rows[0]]
    return [{h: ("" if v is None else (v.strip() if isinstance(v, str) else v)) for h, v in zip(headers, r)} for r in rows[1:]]


def _s(row, key) -> str:
    v = row.get(key, "")
    return str(v).strip() if v not in (None,) else ""


def _int(row, key, default, lo=0, hi=1000):
    raw = _s(row, key)
    if raw == "":
        return default, None
    try:
        n = int(float(raw))
    except ValueError:
        return default, f"{key} must be a whole number"
    return (n, None) if lo <= n <= hi else (default, f"{key} must be between {lo} and {hi}")


def _date(row, key):
    v = row.get(key, "")
    if v in ("", None):
        return None, None
    if hasattr(v, "date"):
        return v.date().isoformat(), None
    if hasattr(v, "isoformat"):
        return v.isoformat(), None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return timezone.datetime.strptime(str(v).strip(), fmt).date().isoformat(), None
        except ValueError:
            continue
    return None, f"{key} must be a date like 2026-08-14 or 14/08/2026"


class Importer:
    kind = ""
    label = ""
    description = ""
    columns: list[tuple[str, bool, str]] = []        # (name, required, help)
    sample = ""

    def validate(self, row: dict) -> tuple[dict, list[str]]:
        raise NotImplementedError

    def commit(self, clean: dict, user) -> tuple[str, list[str]]:
        """Returns ('created'|'updated'|'skipped', [created refs])."""
        raise NotImplementedError

    def check_batch(self, valid_rows: list[dict]) -> list[str]:
        return []

    def commit_all(self, batch, valid_rows, user):
        return None

    @property
    def header(self):
        return ",".join(c[0] for c in self.columns)

    def template_csv(self) -> str:
        return self.header + "\n" + self.sample + "\n"


class ClientImporter(Importer):
    kind, label = "clients", "Clients / guests"
    description = "Guests with phone and e-mail. Existing clients (same phone or e-mail) are updated, not duplicated."
    columns = [("full_name", True, ""), ("phone", True, "Any Kenyan or international format"), ("email", False, ""), ("nationality", False, ""), ("notes", False, "")]
    sample = "Wanjiru Kamau,0712 345 678,wanjiru@example.com,Kenyan,Prefers private vehicles"

    def validate(self, row):
        errs = []
        name, phone, email = _s(row, "full_name"), normalise_phone(_s(row, "phone")), _s(row, "email").lower()
        if not name:
            errs.append("full_name is required")
        if len(phone.lstrip("+")) < 9:
            errs.append("phone is missing or not a valid number")
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errs.append("email is not valid")
        return {"full_name": name, "phone": phone, "email": email, "nationality": _s(row, "nationality"), "notes": _s(row, "notes")}, errs

    def commit(self, c, user):
        from apps.contacts.models import Client
        from apps.leads.services import find_existing_client

        existing = find_existing_client(c["phone"], c["email"])
        if existing:
            changed = False
            for f in ("email", "nationality", "notes"):
                if c[f] and not getattr(existing, f):
                    setattr(existing, f, c[f])
                    changed = True
            if changed:
                existing.save()
                return "updated", []
            return "skipped", []
        obj = Client.objects.create(full_name=c["full_name"], phone=c["phone"], email=c["email"], nationality=c["nationality"] or "Kenyan", notes=c["notes"])
        return "created", [f"contacts.Client:{obj.pk}"]


class LeadImporter(Importer):
    kind, label = "leads", "Leads / enquiries"
    description = "Enquiries from a spreadsheet, a trade-show list or another CRM. Each becomes an unassigned enquiry for the sales team."
    columns = [("full_name", True, ""), ("phone", True, ""), ("email", False, ""), ("source", False, "facebook, whatsapp, website, call… (default website)"),
               ("package_code", False, "e.g. PKG-0003"), ("adults", False, "default 2"), ("children", False, "default 0"),
               ("travel_date", False, "2026-08-14 or 14/08/2026"), ("notes", False, "")]
    sample = "Emma Thompson,+447700900123,emma@example.com,website,,2,0,2026-08-14,Met us at the expo"

    def validate(self, row):
        from apps.catalog.models import TourPackage
        from apps.leads.models import Source

        errs = []
        name, phone, email = _s(row, "full_name"), normalise_phone(_s(row, "phone")), _s(row, "email").lower()
        if not name:
            errs.append("full_name is required")
        if len(phone.lstrip("+")) < 9:
            errs.append("phone is missing or not a valid number")
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errs.append("email is not valid")
        source = _s(row, "source").lower().replace(" ", "_").replace("-", "_") or "website"
        from apps.leads.models import source_codes

        if source not in source_codes():
            errs.append(f"source '{source}' is not one of: {', '.join(source_codes())}")
        code = _s(row, "package_code").upper()
        if code and not TourPackage.objects.alive().filter(code__iexact=code).exists():
            errs.append(f"package_code '{code}' not found")
        adults, e1 = _int(row, "adults", 2, 1, 60)
        children, e2 = _int(row, "children", 0, 0, 60)
        when, e3 = _date(row, "travel_date")
        errs += [e for e in (e1, e2, e3) if e]
        return {"full_name": name, "phone": phone, "email": email, "source": source, "package_code": code, "adults": adults,
                "children": children, "travel_date": when, "notes": _s(row, "notes")}, errs

    def commit(self, c, user):
        from datetime import date

        from apps.catalog.models import TourPackage
        from apps.leads import services as lead_services

        known = lead_services.find_existing_client(c["phone"], c["email"])
        pkg = TourPackage.objects.alive().filter(code__iexact=c["package_code"]).first() if c["package_code"] else None
        enq = lead_services.create_enquiry(
            client_data={"full_name": c["full_name"], "phone": c["phone"], "email": c["email"]},
            enquiry_data={"source": c["source"], "package": pkg, "adults": c["adults"], "children": c["children"],
                          "travel_date_from": date.fromisoformat(c["travel_date"]) if c["travel_date"] else None,
                          "notes": c["notes"], "captured_via": "api"}, user=user, auto_assign=False)
        refs = [f"leads.Enquiry:{enq.pk}"]
        if known is None:
            refs.append(f"contacts.Client:{enq.client_id}")
        return "created", refs


class StaffImporter(Importer):
    kind, label = "staff", "Staff"
    description = "Employee records (no login is created). Existing staff with the same e-mail are updated."
    columns = [("full_name", True, ""), ("email", True, ""), ("phone", False, ""), ("title", False, ""), ("department", False, "created if new"),
               ("is_sales_agent", False, "yes/no"), ("is_guide", False, "yes/no"), ("is_driver", False, "yes/no"), ("basic_salary", False, "KES per month")]
    sample = "Faith Chebet,faith@pocketofparadise.co.ke,0722000111,Finance Officer,Finance,no,no,no,85000"

    def validate(self, row):
        errs = []
        name, email = _s(row, "full_name"), _s(row, "email").lower()
        if not name:
            errs.append("full_name is required")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errs.append("email is required and must be valid")
        salary = _s(row, "basic_salary").replace(",", "")
        try:
            sal = str(Decimal(salary)) if salary else "0"
            if Decimal(sal) < 0:
                errs.append("basic_salary can't be negative")
        except InvalidOperation:
            errs.append("basic_salary must be a number")
            sal = "0"
        flags = {k: _s(row, k).lower() in YES for k in ("is_sales_agent", "is_guide", "is_driver")}
        return {"full_name": name, "email": email, "phone": normalise_phone(_s(row, "phone")), "title": _s(row, "title"),
                "department": _s(row, "department"), "basic_salary": sal, **flags}, errs

    def commit(self, c, user):
        from apps.people.models import Department, Employee

        dept = Department.objects.get_or_create(name=c["department"])[0] if c["department"] else None
        emp = Employee.objects.filter(email__iexact=c["email"]).first()
        fields = dict(full_name=c["full_name"], phone=c["phone"], title=c["title"], department=dept, is_sales_agent=c["is_sales_agent"],
                      is_guide=c["is_guide"], is_driver=c["is_driver"], basic_salary=Decimal(c["basic_salary"]))
        if emp:
            for k, v in fields.items():
                if v not in ("", None, False, Decimal("0")):
                    setattr(emp, k, v)
            emp.save()
            return "updated", []
        emp = Employee.objects.create(email=c["email"], **fields)
        return "created", [f"people.Employee:{emp.pk}"]


class SupplierImporter(Importer):
    kind, label = "suppliers", "Suppliers"
    description = "Lodges, camps, transporters, caterers and park-fee agents."
    columns = [("name", True, ""), ("kind", False, "lodge, hotel, transport, caterer, activity, park, airline, other"), ("contact_name", False, ""),
               ("phone", False, ""), ("email", False, ""), ("location", False, ""), ("payment_terms_days", False, "default 30")]
    sample = "Mara Serena Safari Lodge,lodge,Reservations,0711222333,res@maraserena.example,Maasai Mara,30"

    def validate(self, row):
        from apps.suppliers.models import Supplier

        errs = []
        name = _s(row, "name")
        kind = _s(row, "kind").lower() or "other"
        if not name:
            errs.append("name is required")
        if kind not in dict(Supplier.KINDS):
            errs.append(f"kind '{kind}' is not one of: {', '.join(dict(Supplier.KINDS))}")
        days, e = _int(row, "payment_terms_days", 30, 0, 365)
        if e:
            errs.append(e)
        return {"name": name, "kind": kind, "contact_name": _s(row, "contact_name"), "phone": normalise_phone(_s(row, "phone")),
                "email": _s(row, "email").lower(), "location": _s(row, "location"), "payment_terms_days": days}, errs

    def commit(self, c, user):
        from apps.suppliers.models import Supplier

        s = Supplier.objects.filter(name__iexact=c["name"]).first()
        if s:
            changed = False
            for f in ("contact_name", "phone", "email", "location"):
                if c[f] and not getattr(s, f):
                    setattr(s, f, c[f])
                    changed = True
            if changed:
                s.save()
                return "updated", []
            return "skipped", []
        s = Supplier.objects.create(**c)
        return "created", [f"suppliers.Supplier:{s.pk}"]


class OpeningBalanceImporter(Importer):
    kind, label = "opening_balances", "Opening balances"
    description = "One journal entry that sets the starting balance of each account. The file must balance (total debits = total credits)."
    columns = [("account_code", True, "From the chart of accounts, e.g. 1000"), ("debit", False, ""), ("credit", False, ""), ("date", False, "Opening date (default today)")]
    sample = "1000,5000000,,2026-01-01\n3000,,5000000,2026-01-01"

    def validate(self, row):
        from apps.finance import services as fin
        from apps.finance.models import Account

        fin.ensure_chart()
        errs = []
        code = _s(row, "account_code")
        acct = Account.objects.filter(code=code).first()
        if not acct:
            errs.append(f"account_code '{code}' is not in the chart of accounts")
        vals = {}
        for k in ("debit", "credit"):
            raw = _s(row, k).replace(",", "")
            try:
                vals[k] = str(Decimal(raw)) if raw else "0"
                if Decimal(vals[k]) < 0:
                    errs.append(f"{k} can't be negative")
            except InvalidOperation:
                errs.append(f"{k} must be a number")
                vals[k] = "0"
        if Decimal(vals["debit"]) and Decimal(vals["credit"]):
            errs.append("use either debit or credit on a row, not both")
        if not Decimal(vals["debit"]) and not Decimal(vals["credit"]):
            errs.append("enter a debit or a credit")
        when, e = _date(row, "date")
        if e:
            errs.append(e)
        return {"account_key": acct.key if acct else "", "account_code": code, **vals, "date": when}, errs

    def check_batch(self, valid_rows):
        d = sum((Decimal(r["debit"]) for r in valid_rows), Decimal("0"))
        c = sum((Decimal(r["credit"]) for r in valid_rows), Decimal("0"))
        return [] if d == c else [f"The file doesn't balance: debits {d:,.2f} vs credits {c:,.2f} (difference {abs(d - c):,.2f})."]

    def commit(self, c, user):
        return "created", []                       # the whole batch posts as one entry in commit_all

    def commit_all(self, batch, valid_rows, user):
        from datetime import date

        from apps.finance import services as fin

        when = date.fromisoformat(valid_rows[0]["date"]) if valid_rows[0].get("date") else timezone.localdate()
        entry = fin.post(when, f"Opening balances (import #{batch.pk})", [(r["account_key"], Decimal(r["debit"]), Decimal(r["credit"])) for r in valid_rows],
                         ("import", batch.pk, "opening"), user)
        return [f"finance.JournalEntry:{entry.pk}"]


IMPORTERS = {i.kind: i for i in (ClientImporter(), LeadImporter(), StaffImporter(), SupplierImporter(), OpeningBalanceImporter())}


def check(kind: str, filename: str, raw: bytes, user=None) -> ImportBatch:
    """Validate every row and keep the result. Nothing is written to the business tables."""
    imp = IMPORTERS[kind]
    table = read_table(filename, raw)
    have = set(table[0].keys())
    missing = [name for name, required, _ in imp.columns if required and name not in have]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing) + ". Download the template to see the layout.")
    rows, valid = [], []
    for i, raw_row in enumerate(table, start=2):
        clean, errs = imp.validate(raw_row)
        rows.append({"n": i, "clean": clean, "errors": errs})
        if not errs:
            valid.append(clean)
    seen, dupes = set(), 0
    for r in rows:                                          # the same phone twice in one file is flagged, not silently merged
        key = (r["clean"].get("phone") or r["clean"].get("email") or r["clean"].get("name") or "") if kind != "opening_balances" else None
        if key and not r["errors"]:
            if key in seen and kind in ("clients", "leads"):
                r["errors"].append("duplicate of an earlier row in this file")
                dupes += 1
            seen.add(key)
    valid = [r["clean"] for r in rows if not r["errors"]]
    batch_errors = imp.check_batch(valid) if valid else []
    b = ImportBatch.objects.create(kind=kind, filename=filename[:200], rows=rows, total=len(rows), valid=len(valid), invalid=len(rows) - len(valid),
                                   batch_errors=batch_errors, uploaded_by=user)
    audit(user, "import.checked", b, f"{b.total} rows: {b.valid} valid, {b.invalid} with errors", label=f"{imp.label} · {filename}")
    return b


@transaction.atomic
def commit(batch: ImportBatch, user=None) -> ImportBatch:
    if batch.status != ImportBatch.CHECKED:
        raise ValueError("Only a checked batch can be imported.")
    if batch.batch_errors:
        raise ValueError("Fix the file-level problems first: " + " ".join(batch.batch_errors))
    imp = IMPORTERS[batch.kind]
    refs, counts = [], {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
    valid = [r["clean"] for r in batch.rows if not r["errors"]]
    if not valid:
        raise ValueError("There are no valid rows to import.")
    for r in batch.rows:
        if r["errors"]:
            continue
        try:
            with transaction.atomic():
                result, made = imp.commit(r["clean"], user)
        except Exception as exc:                     # one bad row never sinks the batch
            counts["failed"] += 1
            r["errors"].append(f"import failed: {exc}")
            continue
        counts[result] += 1
        refs += made
    extra = imp.commit_all(batch, valid, user)
    if extra:
        refs += extra
        counts["created"] = len(valid)
    batch.status, batch.imported_at, batch.created_refs = ImportBatch.IMPORTED, timezone.now(), refs
    batch.created, batch.updated, batch.skipped, batch.failed = counts["created"], counts["updated"], counts["skipped"], counts["failed"]
    batch.save()
    audit(user, "import.committed", batch, f"{batch.created} created, {batch.updated} updated, {batch.skipped} unchanged, {batch.failed} failed",
          label=f"{imp.label} · {batch.filename}")
    return batch


@transaction.atomic
def undo(batch: ImportBatch, user=None) -> ImportBatch:
    """Hard-delete only what this batch created. Rows it merely updated are not reverted."""
    from django.apps import apps

    if not batch.can_undo:
        raise ValueError("This import can no longer be undone (only imported batches under 7 days old).")
    deleted = 0
    # Dependants first (enquiries before the clients they point at); anything still protected by newer records stays.
    order = {"contacts.Client": 9, "people.Employee": 5, "suppliers.Supplier": 5}
    refs = sorted(batch.created_refs, key=lambda r: order.get(r.rsplit(":", 1)[0], 0))
    for ref in refs:
        label, pk = ref.rsplit(":", 1)
        try:
            deleted += apps.get_model(label).objects.filter(pk=pk).delete()[0]
        except Exception:
            pass
    batch.status, batch.undone_at = ImportBatch.UNDONE, timezone.now()
    batch.save(update_fields=["status", "undone_at"])
    audit(user, "import.undone", batch, f"{deleted} record(s) removed", label=f"{IMPORTERS[batch.kind].label} · {batch.filename}")
    return batch
