"""Agent QR business cards.

Every sales agent has a digital card. Its QR code opens /card/<token>/ (no login): the agent's card with "Save contact" (a vCard),
then a short "tell us about your trip" form. What is sent becomes a lead assigned straight to that agent, who is alerted on WhatsApp.
Opening the card, saving the contact and sending a request are counted, so each agent (and the director and COO) can see what the
card brings in."""
import hashlib
import io
from datetime import date

from django import forms
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts import roles as R
from apps.accounts.permissions import module_required
from apps.core.forms import StyledForm, StyledModelForm

from .models import AgentCard, CardEvent

SALES = "sales"
OVERSEERS = {R.ADMIN, R.DIRECTOR, R.COO, R.SALES_MANAGER}


def card_for(emp) -> AgentCard:
    card, _ = AgentCard.objects.get_or_create(employee=emp, defaults={"headline": emp.title or "Travel consultant"})
    return card


def public_url(card: AgentCard) -> str:
    from apps.notifications.services import absolute

    return absolute(card.public_path)


# ------------------------------------------------------------------ images
def qr_png(url: str, box=12) -> bytes:
    import qrcode

    img = qrcode.make(url, box_size=box, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def qr_svg_b64(url: str) -> str:
    import base64

    import qrcode
    import qrcode.image.svg

    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2).save(buf)
    return base64.b64encode(buf.getvalue()).decode()


def card_png(card: AgentCard) -> bytes:
    """A shareable picture of the card (1050 x 600, business-card proportions) with the QR code on it."""
    from PIL import Image, ImageDraw, ImageFont

    from apps.core.models import CompanySetting

    emp, co = card.employee, CompanySetting.load()
    W, H = 1050, 600
    img = Image.new("RGB", (W, H), "#FBF5EA")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 120], fill="#1F6F6B")

    def font(size, bold=False):
        for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), "Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()

    from pathlib import Path

    from django.conf import settings

    mark = Path(settings.BASE_DIR) / "static" / "brand" / "logo-source.png"
    x = 48
    if mark.exists():                      # the logo emblem on a cream tile, left of the company name
        m = Image.open(mark).convert("RGB").resize((96, 96))
        img.paste(m, (40, 12))
        x = 156
    d.text((x, 38), (co.name or "Pocket of Paradise")[:40], fill="#F6DDA6", font=font(36, True))
    d.text((48, 170), emp.full_name[:32], fill="#2B2014", font=font(54, True))
    d.text((48, 240), (card.headline or emp.title or "Travel consultant")[:46], fill="#7A5A35", font=font(28))
    y = 320
    for line in [l for l in (emp.phone and f"Phone / WhatsApp: {emp.phone}", (emp.email or getattr(emp.user, 'email', '')) and f"E-mail: {emp.email or emp.user.email}",
                             co.website and co.website.replace('https://', '').replace('http://', '')) if l]:
        d.text((48, y), line[:48], fill="#2B2014", font=font(28))
        y += 46
    d.text((48, H - 70), "Scan to save my contact and plan your trip", fill="#7A5A35", font=font(24))
    qr = Image.open(io.BytesIO(qr_png(public_url(card), 10))).convert("RGB").resize((360, 360))
    img.paste(qr, (W - 360 - 48, 170))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


BRAND_TAGLINE = "Where everyday is an exploration"


def card_back_png(card: AgentCard) -> bytes:
    """The back of the card (same 1050 x 600 size as the front): logo, company name, tagline and website on brand green."""
    from pathlib import Path

    from django.conf import settings
    from PIL import Image, ImageDraw, ImageFont

    from apps.core.models import CompanySetting

    co = CompanySetting.load()
    W, H = 1050, 600
    img = Image.new("RGB", (W, H), "#1F6F6B")
    d = ImageDraw.Draw(img)

    def font(size, bold=False):
        for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), "Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()

    def centred(y, text, fill, f):
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, fill=fill, font=f)

    mark = Path(settings.BASE_DIR) / "static" / "brand" / "logo-source.png"
    y = 70
    if mark.exists():                      # the logo emblem on its cream tile, centred
        m = Image.open(mark).convert("RGB").resize((220, 220))
        img.paste(m, ((W - 220) // 2, y))
        y += 250
    else:
        y += 120
    centred(y, (co.name or "Pocket of Paradise")[:40], "#F6DDA6", font(40, True))
    centred(y + 64, BRAND_TAGLINE, "#F9EDD7", font(26))
    d.rectangle([(W - 120) // 2, y + 118, (W + 120) // 2, y + 122], fill="#D98A22")   # the logo's sun, as a rule
    if co.website:
        centred(H - 70, co.website.replace("https://", "").replace("http://", "").rstrip("/"), "#F9EDD7", font(24))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def vcard(card: AgentCard) -> str:
    from apps.core.models import CompanySetting

    emp, co = card.employee, CompanySetting.load()
    parts = emp.full_name.split()
    last, first = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (emp.full_name, "")

    def esc(v):
        return (v or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    lines = ["BEGIN:VCARD", "VERSION:3.0", f"N:{esc(last)};{esc(first)};;;", f"FN:{esc(emp.full_name)}", f"ORG:{esc(co.name)}",
             f"TITLE:{esc(card.headline or emp.title or 'Travel consultant')}"]
    if emp.phone:
        lines.append(f"TEL;TYPE=CELL,VOICE:{emp.phone}")
    email = emp.email or (emp.user.email if emp.user_id else "")
    if email:
        lines.append(f"EMAIL;TYPE=WORK:{email}")
    if co.website:
        lines.append(f"URL:{co.website}")
    lines.append(f"NOTE:{esc('Your travel consultant at ' + (co.name or 'Pocket of Paradise'))}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ------------------------------------------------------------------ counting
def _visitor(request) -> str:
    ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", ""))
    return hashlib.sha256(f"{ip}|{request.META.get('HTTP_USER_AGENT', '')[:200]}".encode()).hexdigest()[:32]


def record(card, kind, request, enquiry=None):
    """Count one event; a refresh by the same visitor within 30 minutes is not counted twice (leads always count)."""
    v = _visitor(request)
    key = f"card:{card.pk}:{kind}:{v}"
    if kind != CardEvent.LEAD and cache.get(key):
        return None
    cache.set(key, 1, 1800)
    return CardEvent.objects.create(card=card, kind=kind, visitor=v, enquiry=enquiry)


# ------------------------------------------------------------------ the lead
class TripRequestForm(StyledForm):
    name = forms.CharField(label="Your name", max_length=120)
    phone = forms.CharField(label="Phone / WhatsApp", max_length=20)
    email = forms.EmailField(label="E-mail", required=False)
    destination = forms.CharField(label="Where would you like to go?", max_length=160, widget=forms.TextInput(attrs={"placeholder": "e.g. Maasai Mara and Diani"}))
    date_from = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="To", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    adults = forms.IntegerField(label="Adults", min_value=1, max_value=60, initial=2)
    children = forms.IntegerField(label="Children", min_value=0, max_value=30, initial=0, required=False)
    budget = forms.CharField(label="Budget (optional)", max_length=60, required=False, widget=forms.TextInput(attrs={"placeholder": "e.g. USD 1,500 per person"}))
    details = forms.CharField(label="Anything else we should know?", required=False, max_length=1500, widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Honeymoon, a special occasion, dietary needs, the kind of lodge you like…"}))
    website = forms.CharField(required=False, widget=forms.HiddenInput)     # spam trap: people never fill it

    def clean_phone(self):
        from apps.core.phone import normalise_phone

        p = normalise_phone(self.cleaned_data["phone"])
        if len(p) < 10:
            raise forms.ValidationError("Please enter a phone number we can reach you on.")
        return p

    def clean(self):
        d = super().clean()
        if d.get("date_from") and d.get("date_to") and d["date_to"] < d["date_from"]:
            self.add_error("date_to", "The end date is before the start date.")
        if d.get("date_from") and d["date_from"] < date.today():
            self.add_error("date_from", "Choose a date from today on.")
        return d


@transaction.atomic
def create_card_lead(card: AgentCard, d: dict):
    """The request becomes a lead for this agent (or, if they are away, waits at the Front Desk with the agent noted)."""
    from apps.leads import services as ls
    from apps.notifications.services import notify, notify_roles

    agent = card.employee
    notes = "\n".join(x for x in [f"Wants to go: {d['destination']}", d.get("budget") and f"Budget: {d['budget']}", d.get("details") or "",
                                  f"(Came through {agent.full_name}'s QR business card)"] if x)
    enquiry = ls.create_enquiry(
        client_data={"full_name": d["name"].strip(), "phone": d["phone"], "email": d.get("email") or ""},
        enquiry_data={"source": "agent_card", "captured_via": "agent_card", "destination_text": d["destination"][:160], "budget_text": (d.get("budget") or "")[:60],
                      "adults": d.get("adults") or 2, "children": d.get("children") or 0, "travel_date_from": d.get("date_from"), "travel_date_to": d.get("date_to"),
                      "dates_flexible": not d.get("date_from"), "notes": notes, "brought_by": agent},
        user=None, auto_assign=False)
    try:
        ls.assign_agent(enquiry, agent, None, "card", "Came through the agent's QR card")      # alerts the agent (app + WhatsApp)
    except ValueError as exc:   # on leave / off duty: keep it visible and tell both the agent and the managers
        if agent.user_id:
            transaction.on_commit(lambda: notify(agent.user, f"New lead from your QR card: {enquiry.client.full_name}", f"{d['destination']} · you are {exc}; it waits at the Front Desk.",
                                                 enquiry.get_absolute_url(), "info", dedupe_key=f"cardlead:{enquiry.pk}", purpose="lead_assigned"))
        transaction.on_commit(lambda: notify_roles([R.SALES_MANAGER, R.FRONTDESK_MANAGER], f"QR-card lead for {agent.full_name} needs assigning", f"{enquiry.reference}: {exc}",
                                                   enquiry.get_absolute_url(), "warning", dedupe_key=f"cardlead-mgr:{enquiry.pk}"))
    return enquiry


# ------------------------------------------------------------------ public pages (no login)
def _card_or_404(token):
    card = get_object_or_404(AgentCard.objects.select_related("employee", "employee__user"), token=token)
    if not card.is_active or not card.employee.is_active:
        raise Http404
    return card


def _first(emp) -> str:
    return (emp.full_name or "").split()[0] if emp.full_name else "your consultant"


def _public(resp):
    resp["X-Robots-Tag"] = "noindex"
    return resp


def public_card(request, token):
    from apps.core.models import CompanySetting

    card = _card_or_404(token)
    record(card, CardEvent.SCAN, request)
    emp = card.employee
    return _public(render(request, "sales/card_public.html", {"card": card, "emp": emp, "company": CompanySetting.load(),
                                                              "email": emp.email or (emp.user.email if emp.user_id else ""), "agent_first": _first(emp)}))


def public_card_vcard(request, token):
    card = _card_or_404(token)
    record(card, CardEvent.VCARD, request)
    resp = HttpResponse(vcard(card), content_type="text/vcard; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{card.employee.full_name.replace(" ", "-")}.vcf"'
    return resp


def public_card_plan(request, token):
    from apps.core.models import CompanySetting

    card = _card_or_404(token)
    form = TripRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data.get("website"):
            return redirect("public_card_thanks", token=token)
        ip_key = f"card-post:{_visitor(request)}"
        if cache.get(ip_key, 0) >= 5:
            form.add_error(None, "Thank you, we already have your request. Your consultant will be in touch shortly.")
        else:
            cache.set(ip_key, cache.get(ip_key, 0) + 1, 3600)
            enquiry = create_card_lead(card, form.cleaned_data)
            record(card, CardEvent.LEAD, request, enquiry)
            request.session["card_lead_first"] = form.cleaned_data["name"].split()[0]
            return redirect("public_card_thanks", token=token)
    return _public(render(request, "sales/card_plan.html", {"card": card, "emp": card.employee, "form": form, "company": CompanySetting.load(), "agent_first": _first(card.employee)}))


def public_card_thanks(request, token):
    from apps.core.models import CompanySetting

    card = _card_or_404(token)
    return _public(render(request, "sales/card_thanks.html", {"card": card, "emp": card.employee, "company": CompanySetting.load(),
                                                              "first": request.session.pop("card_lead_first", ""), "agent_first": _first(card.employee)}))


def public_card_qr(request, token):
    card = _card_or_404(token)
    resp = HttpResponse(qr_png(public_url(card)), content_type="image/png")
    if request.GET.get("download"):
        resp["Content-Disposition"] = f'attachment; filename="qr-{card.employee.full_name.replace(" ", "-")}.png"'
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


def public_card_image(request, token):
    card = _card_or_404(token)
    back = request.GET.get("side") == "back"
    resp = HttpResponse(card_back_png(card) if back else card_png(card), content_type="image/png")
    if request.GET.get("download"):
        side = "back" if back else "front"
        resp["Content-Disposition"] = f'attachment; filename="card-{card.employee.full_name.replace(" ", "-")}-{side}.png"'
    resp["Cache-Control"] = "public, max-age=600"
    return resp


# ------------------------------------------------------------------ staff pages
class CardForm(StyledModelForm):
    class Meta:
        model = AgentCard
        fields = ["headline", "bio", "photo", "is_active"]
        labels = {"is_active": "Card is switched on (the QR code works)"}
        widgets = {"bio": forms.Textarea(attrs={"rows": 3})}


def _performance(emp, days=90):
    from datetime import timedelta

    from apps.sales import analytics

    end = timezone.localdate()
    rows = analytics.agent_rows(end - timedelta(days=days), end, [emp])
    return rows[0] if rows else {}


def _card_context(card):
    events = card.events.select_related("enquiry__client").filter(kind=CardEvent.LEAD)[:20]
    return {"card": card, "emp": card.employee, "stats": card.stats(), "url": public_url(card), "qr": qr_svg_b64(public_url(card)), "leads": events,
            "perf": _performance(card.employee)}


@module_required(SALES)
def my_card(request):
    emp = getattr(request.user, "employee", None)
    if emp is None:
        messages.error(request, "Your login is not linked to a staff record, so there is no card to show. Ask HR to link it.")
        return redirect("sales:dashboard")
    card = card_for(emp)
    form = CardForm(request.POST or None, request.FILES or None, instance=card)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Card saved.")
        return redirect("sales:my_card")
    from urllib.parse import quote

    ctx = _card_context(card)
    ctx.update(form=form, share=quote(f"Hello! Here is my card: save my contact and tell me about the trip you have in mind. {ctx['url']}"))
    return render(request, "sales/card_mine.html", ctx)


@module_required(SALES)
def cards_overview(request):
    if not request.user.has_role(*OVERSEERS):
        raise PermissionDenied
    from apps.people.models import Employee

    for emp in Employee.objects.filter(is_sales_agent=True, is_active=True, card__isnull=True):
        card_for(emp)
    cards = list(AgentCard.objects.select_related("employee").filter(employee__is_active=True).order_by("employee__full_name"))
    rows = [{"card": c, "stats": c.stats()} for c in cards]
    totals = {k: sum(r["stats"][k] for r in rows) for k in ("scans", "saved", "leads", "won")}
    totals["conversion"] = round(100 * totals["won"] / totals["leads"]) if totals["leads"] else 0
    rows.sort(key=lambda r: (-r["stats"]["leads"], -r["stats"]["scans"]))
    return render(request, "sales/cards_overview.html", {"rows": rows, "totals": totals})


@module_required(SALES)
def card_detail(request, pk):
    """The pop-up on the overview: the agent's card, its numbers, the leads it brought and the agent's 90-day performance."""
    card = get_object_or_404(AgentCard.objects.select_related("employee"), pk=pk)
    if not (request.user.has_role(*OVERSEERS) or card.employee.user_id == request.user.pk):
        raise PermissionDenied
    return render(request, "sales/_card_detail.html", _card_context(card))
