from datetime import date as _date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import module_required

from . import bank
from .models import Account, BankReconciliation, BankStatementLine

FIN = "finance"


def _need(user):
    if not user.can_manage_finance:
        raise PermissionDenied


def _account(request):
    accounts = list(bank.bank_accounts())
    code = request.GET.get("account") or request.POST.get("account")
    acct = next((a for a in accounts if a.code == code), accounts[0] if accounts else None)
    if acct is None:
        raise PermissionDenied
    return acct, accounts


def _back(acct, tab=""):
    return redirect(f"{reverse('finance:bank')}?account={acct.code}" + (f"&tab={tab}" if tab else ""))


@module_required(FIN)
def bank_view(request):
    from apps.finance import services

    services.ensure_chart()
    acct, accounts = _account(request)
    tab = request.GET.get("tab", "unmatched")
    lines = BankStatementLine.objects.filter(account=acct).select_related("journal_line__entry")
    if tab in ("unmatched", "matched", "ignored"):
        lines = lines.filter(status=tab)
    lines = list(lines[:60])
    for ln in lines:
        ln.suggestions = bank.candidates(ln)[:3] if ln.status == "unmatched" else []
    counts = {k: BankStatementLine.objects.filter(account=acct, status=k).count() for k in ("unmatched", "matched", "ignored")}
    return render(request, "finance/bank.html", {
        "account": acct, "accounts": accounts, "tab": tab, "lines": lines, "counts": counts, "s": bank.summary(acct, timezone.localdate()),
        "tabs": [("unmatched", "To explain"), ("matched", "Matched"), ("ignored", "Ignored")],
        "recs": BankReconciliation.objects.filter(account=acct).select_related("reconciled_by")[:6], "today": timezone.localdate(),
        "can_act": request.user.can_manage_finance})


@require_POST
@module_required(FIN)
def bank_import(request):
    _need(request.user)
    acct, _ = _account(request)
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose the bank statement file.")
    else:
        try:
            r = bank.import_statement(acct, f.name, f.read(), request.user)
        except bank.BankError as exc:
            messages.error(request, str(exc))
        else:
            msg = f"Statement imported: {r['created']} new line(s), {r['duplicates']} already imported, {r['matched']} matched automatically."
            if r["errors"]:
                msg += f" {len(r['errors'])} row(s) skipped, e.g. {r['errors'][0]}"
            messages.success(request, msg)
    return _back(acct)


@require_POST
@module_required(FIN)
def bank_auto(request):
    _need(request.user)
    acct, _ = _account(request)
    messages.success(request, f"{bank.auto_match(acct, request.user)} line(s) matched automatically.")
    return _back(acct)


@require_POST
@module_required(FIN)
def bank_line_action(request, pk):
    _need(request.user)
    line = get_object_or_404(BankStatementLine, pk=pk)
    act = request.POST.get("action")
    try:
        if act == "match":
            bank.match(line, int(request.POST.get("journal_line") or 0), request.user)
            messages.success(request, "Matched.")
        elif act == "unmatch":
            bank.unmatch(line)
        elif act == "ignore":
            bank.ignore(line, request.POST.get("reason", "").strip(), request.user)
            messages.success(request, "Line ignored.")
    except (bank.BankError, ValueError) as exc:
        messages.error(request, str(exc))
    return _back(line.account)


@require_POST
@module_required(FIN)
def bank_reconcile(request):
    _need(request.user)
    acct, _ = _account(request)
    try:
        d = _date.fromisoformat(request.POST.get("statement_date") or "")
        bal = Decimal((request.POST.get("statement_balance") or "").replace(",", ""))
    except (ValueError, InvalidOperation):
        messages.error(request, "Enter the statement date and the closing balance on the statement.")
        return _back(acct)
    rec = bank.reconcile(acct, d, bal, request.user, request.POST.get("notes", ""))
    (messages.success if rec.balanced else messages.warning)(
        request, "Reconciled: the bank and the books agree." if rec.balanced else f"Saved, but the bank and the books differ by KES {rec.difference:,.2f}. Check the lines still to explain.")
    return _back(acct)
