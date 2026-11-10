"""The public website-form webhook: any form on the company website posts here and becomes an enquiry at the Front Desk.

POST /webhooks/lead/   (form-encoded or JSON)   token in the X-Webhook-Secret header, `Authorization: Bearer …`, `?token=` or a `token` field.
Fields are matched loosely so Contact Form 7, Elementor, Wix, Webflow and hand-written forms all work without renaming."""
import hmac
import json
import secrets

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone

from apps.leads.models import Source, source_codes
from apps.system.services import get_credential, save_credentials

from .models import WebhookDelivery

NAME_KEYS = ("name", "full_name", "fullname", "your-name", "your_name", "fname", "first_name", "contact_name")
PHONE_KEYS = ("phone", "phone_number", "tel", "telephone", "mobile", "whatsapp", "your-phone", "your_phone", "your-tel", "contact_phone")
EMAIL_KEYS = ("email", "e-mail", "your-email", "your_email", "mail", "contact_email")
MESSAGE_KEYS = ("message", "comments", "comment", "enquiry", "your-message", "your_message", "notes", "details", "question")
PACKAGE_KEYS = ("package_code", "package", "tour", "tour_code", "safari", "package_id")
DATE_KEYS = ("travel_date", "date", "arrival", "arrival_date", "departure_date", "when", "your-date")
RATE_LIMIT_PER_HOUR = 120


def website_token() -> str:
    """The saved token; created on first use so the hub page always has one to show."""
    tok = get_credential("webhooks", "website_token")
    if not tok:
        tok = secrets.token_urlsafe(24)
        save_credentials("webhooks", {"website_token": tok})
    return tok


def rotate_token(user=None, request=None) -> str:
    tok = secrets.token_urlsafe(24)
    from apps.system.models import IntegrationCredential

    row, _ = IntegrationCredential.objects.get_or_create(provider="webhooks")
    vals = row.values
    vals["website_token"] = tok
    import json as _j

    row.payload = _j.dumps(vals)
    row.updated_by, row.updated_at = user, timezone.now()
    row.save()
    from apps.system.services import audit

    audit(user, "webhook.token_rotated", row, "Website form token replaced", request, label="Website form webhook")
    return tok


def allowed_origins() -> list[str]:
    raw = get_credential("webhooks", "allowed_origins")
    return [o.strip().rstrip("/") for o in raw.replace("\n", ",").split(",") if o.strip()]


def _cors(resp, request):
    origin = request.headers.get("Origin", "")
    allowed = allowed_origins()
    if origin and (not allowed or origin.rstrip("/") in allowed):
        resp["Access-Control-Allow-Origin"] = origin
        resp["Vary"] = "Origin"
    resp["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp["Access-Control-Allow-Headers"] = "Content-Type, X-Webhook-Secret, Authorization"
    resp["Access-Control-Max-Age"] = "600"
    return resp


def _client_ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None)


def _pick(data: dict, keys) -> str:
    lowered = {str(k).lower(): v for k, v in data.items()}
    for k in keys:
        v = lowered.get(k)
        if v not in (None, ""):
            return (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)).strip()
    return ""


def _int(v, default, lo, hi):
    try:
        return max(lo, min(int(float(v)), hi))
    except (TypeError, ValueError):
        return default


def _body(request) -> dict:
    ctype = request.content_type or ""
    if "json" in ctype:
        try:
            d = json.loads(request.body or b"{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            raise ValueError("The body is not valid JSON.")
    return {k: (v if len(v) > 1 else v[0]) for k, v in request.POST.lists()}


def _token_ok(request, data: dict) -> bool:
    expected = get_credential("webhooks", "website_token") or ""
    legacy = ""
    try:
        from django.conf import settings

        legacy = settings.WEBHOOK_SECRET or ""
    except Exception:
        pass
    given = (request.headers.get("X-Webhook-Secret") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
             or request.GET.get("token") or str(data.get("token", "")))
    return bool(given) and any(e and hmac.compare_digest(given, e) for e in (expected, legacy))


def _log(request, ok, code, summary, enquiry=None, test=False):
    WebhookDelivery.objects.create(source=WebhookDelivery.WEBSITE, ok=ok, status_code=code, ip=_client_ip(request), summary=summary[:240], enquiry=enquiry, is_test=test)


def receive_website(request):
    """The view body. Returns JSON (201) for scripts, or a redirect when the form supplies `redirect` / `_next` / `thank_you_url`."""
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204), request)
    if request.method != "POST":
        return _cors(JsonResponse({"error": "POST only"}, status=405), request)
    ip = _client_ip(request) or "?"
    key = f"rl:webhook:{ip}"
    n = cache.get(key, 0) + 1
    cache.set(key, n, 3600)
    if n > RATE_LIMIT_PER_HOUR:
        return _cors(JsonResponse({"error": "rate limited"}, status=429), request)
    try:
        data = _body(request)
    except ValueError as e:
        _log(request, False, 400, str(e))
        return _cors(JsonResponse({"error": str(e)}, status=400), request)
    if not _token_ok(request, data):
        _log(request, False, 401, "Wrong or missing token")
        return _cors(JsonResponse({"error": "unauthorised"}, status=401), request)
    if _pick(data, ("website", "url_confirm", "hp", "honeypot")):                      # honeypot filled: pretend success, create nothing
        return _cors(JsonResponse({"ok": True}, status=201), request)
    name, phone, email = _pick(data, NAME_KEYS), _pick(data, PHONE_KEYS), _pick(data, EMAIL_KEYS)
    if not name or not (phone or email):
        _log(request, False, 400, "Needs a name and a phone number or e-mail")
        return _cors(JsonResponse({"error": "name and a phone number or e-mail are required"}, status=400), request)
    enquiry = create_lead(data, name, phone, email, test=bool(data.get("_test")))
    _log(request, True, 201, f"{enquiry.reference} · {name}", enquiry, test=bool(data.get("_test")))
    nxt = str(data.get("redirect") or data.get("_next") or data.get("thank_you_url") or "")
    if nxt.startswith(("http://", "https://")):
        return HttpResponseRedirect(nxt)
    return _cors(JsonResponse({"ok": True, "reference": enquiry.reference, "assigned": bool(enquiry.assigned_agent_id), "message": "Thank you! A travel consultant will contact you shortly."}, status=201), request)


def create_lead(data: dict, name: str, phone: str, email: str, test=False):
    from apps.catalog.models import TourPackage
    from apps.marketing import services as ms
    from apps.marketing.models import Campaign

    src = _pick(data, ("source", "utm_source")).lower()
    source = src if src in source_codes() else ("facebook" if src in ("fb", "facebook.com") else "instagram" if src in ("ig", "instagram.com") else "website")
    code = _pick(data, PACKAGE_KEYS)
    pkg = TourPackage.objects.alive().filter(code__iexact=code).first() or (TourPackage.objects.alive().filter(title__icontains=code).first() if code and not code.isdigit() else None)
    camp_name = _pick(data, ("utm_campaign", "campaign"))
    campaign = Campaign.objects.filter(name__iexact=camp_name).first() if camp_name else None
    date_raw = _pick(data, DATE_KEYS)
    travel = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            travel = timezone.datetime.strptime(date_raw[:10], fmt).date()
            break
        except ValueError:
            continue
    extras = [f"{k}: {data[k]}" for k in ("page_url", "utm_medium", "utm_content", "utm_term") if data.get(k)]
    msg = (_pick(data, MESSAGE_KEYS) + ((" [" + "; ".join(extras) + "]") if extras else ""))[:1000]
    if test:
        msg = "(Test lead) " + msg
    return ms.ingest_lead(name=name[:120], phone=phone, email=email, source=source, message=msg, package=pkg,
                          adults=_int(_pick(data, ("adults", "guests", "travellers", "travelers", "people", "pax")), 2, 1, 60),
                          children=_int(_pick(data, ("children", "kids")), 0, 0, 30), travel_date=travel, campaign=campaign, captured_via="website_form")


def snippets(base_url: str, token: str) -> dict:
    url = f"{base_url}/webhooks/lead/"
    return {
        "url": url,
        "html": f'''<form action="{url}" method="post">
  <input type="hidden" name="token" value="{token}">
  <input type="hidden" name="redirect" value="https://YOUR-SITE.com/thank-you">
  <input type="text" name="website" style="display:none" tabindex="-1" autocomplete="off"> <!-- spam trap, leave empty -->
  <input name="name" placeholder="Your name" required>
  <input name="phone" placeholder="Phone / WhatsApp" required>
  <input name="email" type="email" placeholder="E-mail">
  <input name="adults" type="number" min="1" value="2">
  <input name="travel_date" type="date">
  <select name="package_code"><option value="">Choose a safari…</option><option value="PKG-0001">3-Day Maasai Mara</option></select>
  <textarea name="message" placeholder="Tell us about your trip"></textarea>
  <button type="submit">Send enquiry</button>
</form>''',
        "js": f'''fetch("{url}", {{
  method: "POST",
  headers: {{ "Content-Type": "application/json", "X-Webhook-Secret": "{token}" }},
  body: JSON.stringify({{ name, phone, email, message, package_code, adults, travel_date, utm_campaign }})
}}).then(r => r.json()).then(console.log);''',
        "curl": f'''curl -X POST "{url}" \\
  -H "Content-Type: application/json" -H "X-Webhook-Secret: {token}" \\
  -d '{{"name":"Test Traveller","phone":"0712345678","email":"t@example.com","message":"Mara in July?","adults":2}}\'''',
    }
