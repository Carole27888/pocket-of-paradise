import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.accounts import roles as R
from apps.accounts.permissions import module_required

from . import services
from .forms import MailboxForm
from .models import EmailTemplate, Mailbox, MailSettings, SentEmail


@module_required("me", "sales")
def setup(request):
    box = Mailbox.objects.filter(user=request.user).first()
    if request.method == "POST" and request.POST.get("action") == "disconnect" and box:
        box.delete()
        messages.success(request, "Your mailbox was disconnected.")
        return redirect("mailbox:setup")
    initial = {"display_name": request.user.full_name or "", "email": request.user.email}
    form = MailboxForm(request.POST or None, instance=box, initial=initial)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.user = request.user
        obj.save()
        act = request.POST.get("action")
        try:
            if act == "test_imap":
                services.test_imap(obj)
                messages.success(request, "Reading your inbox works.")
            else:
                services.send_test(obj)
                messages.success(request, "Connected. A test email was sent to yourself.")
        except services.MailError as exc:
            messages.error(request, str(exc))
        return redirect("mailbox:setup")
    return render(request, "mailbox/setup.html", {"form": form, "box": box, "cc": MailSettings.load()})


@module_required("me", "sales")
def inbox(request):
    box = Mailbox.objects.filter(user=request.user).first()
    folder = request.GET.get("folder", "inbox")
    folder = folder if folder in ("inbox", "drafts") else "inbox"
    mails, error = [], ""
    if box and box.is_imap_ready:
        try:
            mails = services.fetch_folder(box, folder)
        except services.MailError as exc:
            error = str(exc)
    return render(request, "mailbox/inbox.html", {"box": box, "folder": folder, "mails": mails, "error": error, "sent": SentEmail.objects.filter(sender=request.user)[:8]})


@module_required("me", "sales")
def sent(request):
    return render(request, "mailbox/sent.html", {"items": SentEmail.objects.filter(sender=request.user).select_related("enquiry")[:100]})


@module_required("me", "sales")
def compose(request):
    from apps.leads.views import visible_enquiries

    services.ensure_templates()
    enq = None
    raw = request.GET.get("enquiry") or request.POST.get("enquiry") or ""
    if raw.isdigit():
        enq = visible_enquiries(request.user).select_related("client", "destination").filter(pk=int(raw)).first()
    modal = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    templates = list(EmailTemplate.objects.filter(is_active=True))
    fields = services.merge_fields(enq, request.user)
    rendered = [{"id": t.pk, "name": t.name, "subject": t.subject.format_map(services._Blank(fields)), "body": t.body.format_map(services._Blank(fields))} for t in templates]
    error = ""
    if request.method == "POST":
        try:
            files = request.FILES.getlist("files")
            services.send_client_email(user=request.user, to=request.POST.get("to", "").strip(), subject=request.POST.get("subject", ""), body=request.POST.get("body", ""), lead=enq, files=files)
            messages.success(request, "Email sent.")
            if modal:
                return JsonResponse({"ok": True})
            return redirect(enq.get_absolute_url() if enq else reverse("mailbox:sent"))
        except services.MailError as exc:
            error = str(exc)
    ctx = {"enq": enq, "tpl_json": json.dumps(rendered), "error": error, "box": Mailbox.objects.filter(user=request.user).first(), "cc": MailSettings.load().cc_list(),
           "to": request.POST.get("to") or (enq.client.email if enq else ""), "subject": request.POST.get("subject", ""), "body": request.POST.get("body", "")}
    if not modal:
        return render(request, "mailbox/compose_page.html", ctx)
    return render(request, "mailbox/compose.html", ctx, status=422 if error else 200)


@module_required("me", "sales")
def settings_view(request):
    if not (request.user.is_superuser or request.user.has_role(R.ADMIN, R.COO, R.DIRECTOR)):
        raise PermissionDenied
    cfg = MailSettings.load()
    if request.method == "POST":
        cfg.director_cc = request.POST.get("director_cc", "").strip()
        cfg.cc_enabled = bool(request.POST.get("cc_enabled"))
        cfg.updated_by = request.user
        cfg.save()
        messages.success(request, "Saved.")
        return redirect("mailbox:settings")
    return render(request, "mailbox/settings.html", {"cfg": cfg})
