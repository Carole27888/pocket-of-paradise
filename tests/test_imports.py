import io

import pytest
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts import roles as R
from apps.contacts.models import Client
from apps.finance.models import JournalEntry
from apps.leads.models import Enquiry
from apps.people.models import Employee
from apps.suppliers.models import Supplier
from apps.system import imports
from apps.system.models import AuditLog, ImportBatch


def _csv(text):
    return text.strip().encode()


@pytest.mark.django_db
def test_check_validates_every_row_and_saves_nothing():
    raw = _csv("""full_name,phone,email,nationality
Wanjiru Kamau,0712 345 678,w@example.com,Kenyan
,0712000000,,
Bad Phone,12,,
Bad Mail,0711000111,not-an-email,
Wanjiru Again,0712345678,,
""")
    b = imports.check("clients", "guests.csv", raw)
    assert (b.total, b.valid, b.invalid, b.status) == (5, 1, 4, "checked")
    errs = {r["n"]: r["errors"] for r in b.rows}
    assert "full_name is required" in errs[3][0] and "phone" in errs[4][0] and "email" in errs[5][0] and "duplicate" in errs[6][0]
    assert Client.objects.count() == 0                                       # a check never writes business data


@pytest.mark.django_db
def test_missing_columns_and_bad_files_are_rejected_clearly():
    with pytest.raises(ValueError, match="Missing required column"):
        imports.check("clients", "x.csv", _csv("name,tel\nA,1"))
    with pytest.raises(ValueError, match="header row"):
        imports.check("clients", "x.csv", _csv("full_name,phone"))
    with pytest.raises(ValueError, match="csv or .xlsx"):
        imports.check("clients", "x.pdf", b"%PDF")
    with pytest.raises(ValueError, match="Excel"):
        imports.check("clients", "x.xlsx", b"not a zip")


@pytest.mark.django_db
def test_xlsx_files_work_too():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Full Name", "Phone", "Email"])
    ws.append(["Excel Person", "0733000111", "e@example.com"])
    buf = io.BytesIO()
    wb.save(buf)
    b = imports.check("clients", "g.xlsx", buf.getvalue())
    assert b.valid == 1 and b.rows[0]["clean"]["phone"] == "+254733000111"


@pytest.mark.django_db
def test_commit_creates_updates_and_undo_removes_only_what_was_created():
    existing = Client.objects.create(full_name="Old Client", phone="0722111222")
    b = imports.check("clients", "g.csv", _csv("full_name,phone,email,nationality\nNew One,0733000001,n@example.com,Kenyan\nOld Client,0722 111 222,old@example.com,German\n"))
    imports.commit(b)
    existing.refresh_from_db()
    assert (b.created, b.updated, b.status) == (1, 1, "imported") and existing.email == "old@example.com" and existing.nationality in ("German", "Kenyan")
    assert Client.objects.count() == 2
    with pytest.raises(ValueError, match="checked batch"):
        imports.commit(b)                                                     # can't import twice
    imports.undo(b)
    assert b.status == "undone" and Client.objects.count() == 1 and Client.objects.get().pk == existing.pk
    existing.refresh_from_db()
    assert existing.email == "old@example.com"                                # updates are not reverted
    with pytest.raises(ValueError, match="no longer be undone"):
        imports.undo(b)


@pytest.mark.django_db
def test_undo_window_closes_after_seven_days():
    from datetime import timedelta
    from django.utils import timezone

    b = imports.check("clients", "g.csv", _csv("full_name,phone\nA B,0733000009\n"))
    imports.commit(b)
    ImportBatch.objects.filter(pk=b.pk).update(imported_at=timezone.now() - timedelta(days=8))
    b.refresh_from_db()
    assert not b.can_undo


@pytest.mark.django_db
def test_leads_import_creates_unassigned_enquiries_and_undoes_cleanly(agent):
    from apps.catalog.models import TourPackage

    TourPackage.objects.create(title="Mara", days=3, nights=2, status="published", code="PKG-9")
    b = imports.check("leads", "expo.csv", _csv("""full_name,phone,email,source,package_code,adults,travel_date,notes
Emma T,+447700900123,emma@example.com,facebook,PKG-9,3,2026-08-14,Expo list
Bad Src,0711000333,,carrier pigeon,,2,,
No Pkg,0711000444,,,PKG-404,2,,
Bad Date,0711000555,,,,2,31/31/2026,
Lukas S,+491512345678,,website,,2,14/08/2026,
"""))
    assert b.valid == 2 and b.invalid == 3
    imports.commit(b)
    e = Enquiry.objects.get(client__full_name="Emma T")
    assert e.source == "facebook" and e.adults == 3 and e.package.code == "PKG-9" and e.assigned_agent is None and str(e.travel_date_from) == "2026-08-14"
    assert Enquiry.objects.count() == 2 and Client.objects.count() == 2
    imports.undo(b)
    assert Enquiry.objects.count() == 0 and Client.objects.count() == 0


@pytest.mark.django_db
def test_staff_and_supplier_imports():
    b = imports.check("staff", "s.csv", _csv("full_name,email,title,department,is_guide,basic_salary\nJohn G,john@x.com,Guide,Operations,yes,32000\nBad,notmail,,,,\nNeg,n@x.com,,,,-5\n"))
    assert (b.valid, b.invalid) == (1, 2)
    imports.commit(b)
    e = Employee.objects.get(email="john@x.com")
    assert e.is_guide and e.basic_salary == 32000 and e.department.name == "Operations"
    b2 = imports.check("suppliers", "p.csv", _csv("name,kind,phone,payment_terms_days\nMara Serena,lodge,0711222333,45\nWeird,spaceship,,\n"))
    assert (b2.valid, b2.invalid) == (1, 1)
    imports.commit(b2)
    assert Supplier.objects.get().payment_terms_days == 45


@pytest.mark.django_db
def test_opening_balances_must_balance_and_post_one_entry():
    from apps.finance import services as fin

    bad = imports.check("opening_balances", "ob.csv", _csv("account_code,debit,credit,date\n1000,5000,,2026-01-01\n3000,,4000,2026-01-01\n"))
    assert bad.batch_errors and "doesn't balance" in bad.batch_errors[0]
    with pytest.raises(ValueError, match="file-level"):
        imports.commit(bad)
    good = imports.check("opening_balances", "ob.csv", _csv("account_code,debit,credit,date\n1000,5000,,2026-01-01\n3000,,5000,2026-01-01\n9999,1,,\n"))
    assert good.invalid == 1
    good2 = imports.check("opening_balances", "ob.csv", _csv("account_code,debit,credit,date\n1000,5000,,2026-01-01\n3000,,5000,2026-01-01\n"))
    imports.commit(good2)
    assert JournalEntry.objects.count() == 1 and fin.cash_position() == 5000
    imports.undo(good2)
    assert JournalEntry.objects.count() == 0


@pytest.mark.django_db
def test_screens_access_and_audit(client, make_user):
    admin, coo = make_user("adm@example.com", R.ADMIN), make_user("coo@example.com", R.COO)
    client.force_login(coo)
    assert client.get("/system/import/").status_code == 403
    client.force_login(admin)
    assert client.get("/system/import/").status_code == 200
    for k in imports.IMPORTERS:
        r = client.get(reverse("system:import_template", args=[k]))
        assert r.status_code == 200 and r.content.decode().startswith(imports.IMPORTERS[k].header)
    up = SimpleUploadedFile("g.csv", b"full_name,phone\nA B,0733000009\n", content_type="text/csv")
    r = client.post("/system/import/", {"kind": "clients", "file": up})
    b = ImportBatch.objects.get()
    assert r.status_code == 302 and r.url == reverse("system:import_batch", args=[b.pk])
    assert client.get(r.url).status_code == 200
    client.post(reverse("system:import_action", args=[b.pk]), {"action": "commit"})
    b.refresh_from_db()
    assert b.status == "imported" and Client.objects.count() == 1
    client.post(reverse("system:import_action", args=[b.pk]), {"action": "undo"})
    assert Client.objects.count() == 0
    assert {a.action for a in AuditLog.objects.all()} >= {"import.checked", "import.committed", "import.undone"}
