import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse


def _cell(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    return v


def to_csv(title, cols, rows) -> HttpResponse:
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{title}.csv"'
    w = csv.writer(resp)
    w.writerow(cols)
    for r in rows:
        # defuse spreadsheet formulas in text cells
        w.writerow([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else c for c in r])
    return resp


def xlsx_bytes(title, cols, rows) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = title[:30]
    ws.append(cols)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="4F1410")
    for r in rows:
        ws.append([("'" + c) if isinstance(c, str) and c[:1] in "=+-@" else _cell(c) for c in r])
    for i, col in enumerate(cols, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(12, min(40, max([len(str(col))] + [len(str(r[i - 1])) for r in rows[:50]]) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_xlsx(title, cols, rows) -> HttpResponse:
    resp = HttpResponse(xlsx_bytes(title, cols, rows), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{title}.xlsx"'
    return resp
