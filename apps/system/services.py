"""Audit logging and encrypted integration credentials (with a real 'Test key' call per provider)."""
import json
import os
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from .models import AuditLog, IntegrationCredential


def _ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0] if fwd else request.META.get("REMOTE_ADDR", "")).strip() or None


def audit(actor, action: str, obj=None, detail: str = "", request=None, label: str = "") -> AuditLog:
    """Record a system action. `actor` may be a user or None (jobs/webhooks)."""
    user = actor if getattr(actor, "pk", None) else None
    return AuditLog.objects.create(
        user=user, user_label=(user.full_name if user else "System"), action=action,
        target_type=obj.__class__.__name__ if obj is not None else "", target_id=str(getattr(obj, "pk", "") or ""),
        target_label=(label or (str(obj) if obj is not None else ""))[:200], detail=detail[:2000], ip=_ip(request))


# ============================================================================================== credentials
# provider -> (label, [(field, label, secret?)], env fallbacks {field: ENV_NAME}, what it powers)
PROVIDERS = {
    "ai": ("AI (Anthropic Claude)", [("api_key", "API key", True), ("model", "Model", False), ("prefer", "Preferred provider (claude or openai)", False)],
           {"api_key": "ANTHROPIC_API_KEY", "model": "AI_MODEL", "prefer": "AI_PREFERRED"}, "In-app assistant, the assistant's wording and the WhatsApp bot"),
    "openai": ("AI backup (OpenAI)", [("api_key", "API key", True), ("model", "Model", False)],
               {"api_key": "OPENAI_API_KEY", "model": "OPENAI_MODEL"}, "Used when Claude is not set up or is unavailable (or first, if chosen as preferred)"),
    "whatsapp": ("WhatsApp Business", [("access_token", "Access token (permanent, from a System User)", True), ("phone_number_id", "Phone number ID", False),
                                       ("waba_id", "WhatsApp Business Account ID", False), ("app_secret", "App secret", True),
                                       ("verify_token", "Webhook verify token", True), ("api_version", "Graph API version (optional, e.g. v21.0)", False)],
                 {"access_token": "WHATSAPP_TOKEN", "phone_number_id": "WHATSAPP_PHONE_ID", "waba_id": "WHATSAPP_WABA_ID", "app_secret": "WHATSAPP_APP_SECRET",
                  "verify_token": "WHATSAPP_VERIFY_TOKEN", "api_version": "WHATSAPP_API_VERSION"},
                 "Staff alerts, travel reminders, the lead bot and the director's business assistant"),
    "sms": ("SMS gateway (Africa's Talking)", [("username", "Username (use 'sandbox' to test)", False), ("api_key", "API key", True), ("sender_id", "Sender ID (optional)", False)],
            {"username": "AT_USERNAME", "api_key": "AT_API_KEY", "sender_id": "AT_SENDER_ID"}, "Bulk SMS, reminders and alerts"),
    "brevo": ("E-mail (Brevo)", [("api_key", "API key (v3)", True), ("sender_email", "Sender e-mail (verified in Brevo)", False), ("sender_name", "Sender name", False)],
              {"api_key": "BREVO_API_KEY", "sender_email": "BREVO_SENDER_EMAIL", "sender_name": "BREVO_SENDER_NAME"}, "Bulk e-mail and newsletters"),
    "meta": ("Meta (Facebook, Instagram, WhatsApp)", [("access_token", "Page access token (long-lived)", True), ("app_secret", "App secret", True), ("verify_token", "Webhook verify token", True)],
             {"access_token": "META_ACCESS_TOKEN", "app_secret": "META_APP_SECRET", "verify_token": "META_WEBHOOK_VERIFY_TOKEN"}, "Lead ads, Click-to-WhatsApp, Messenger and Instagram messages"),
    "webhooks": ("Website form webhook", [("website_token", "Website form token", True), ("allowed_origins", "Allowed website origins (optional)", False)],
                 {"website_token": "WEBHOOK_SECRET", "allowed_origins": "WEBHOOK_ALLOWED_ORIGINS"}, "Connects the website's enquiry forms"),
    "tiktok": ("TikTok", [("access_token", "Access token", True)], {"access_token": "TIKTOK_ACCESS_TOKEN"}, "Stats and ads"),
    "google_ads": ("Google Ads", [("access_token", "Access token", True)], {"access_token": "GOOGLE_ADS_TOKEN"}, "Ad spend"),
    "youtube": ("YouTube", [("api_key", "API key", True)], {"api_key": "YOUTUBE_API_KEY"}, "Channel stats"),
    "mpesa": ("M-PESA Daraja", [("consumer_key", "Consumer key", True), ("consumer_secret", "Consumer secret", True),
                                ("shortcode", "Paybill / till", False)],
              {"consumer_key": "MPESA_CONSUMER_KEY", "consumer_secret": "MPESA_CONSUMER_SECRET", "shortcode": "MPESA_SHORTCODE"}, "C2B callbacks"),
}


def get_credential(provider: str, field: str, default: str = "") -> str:
    """Saved (encrypted) value first, then the environment variable, then `default`."""
    row = IntegrationCredential.objects.filter(provider=provider).first()
    if row:
        v = row.values.get(field)
        if v:
            return v
    env = PROVIDERS.get(provider, (None, None, {}))[2].get(field)
    return (os.environ.get(env, "") if env else "") or default


def save_credentials(provider: str, values: dict, user=None, request=None) -> IntegrationCredential:
    """Blank secret fields keep their stored value; the rest overwrite."""
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    row, _ = IntegrationCredential.objects.get_or_create(provider=provider)
    current = row.values
    for name, _label, _secret in PROVIDERS[provider][1]:
        new = (values.get(name) or "").strip()
        if new:
            current[name] = new
    row.payload = json.dumps(current)
    row.updated_by, row.updated_at = user, timezone.now()
    row.last_test_at = row.last_test_ok = None
    row.last_test_message = ""
    row.save()
    audit(user, "credential.saved", row, f"Fields updated: {', '.join(k for k in values if values[k])}", request, label=PROVIDERS[provider][0])
    return row


def delete_credentials(provider: str, user=None, request=None):
    row = IntegrationCredential.objects.filter(provider=provider).first()
    if row:
        audit(user, "credential.removed", row, request=request, label=PROVIDERS[provider][0])
        row.delete()


def mask(value: str) -> str:
    return "••••" + value[-4:] if value and len(value) > 6 else ("••••" if value else "")


def _http(url, headers=None, data=None, method="GET", timeout=15):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:2000].decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:2000].decode("utf-8", "ignore")


def _test_openai(creds):
    key = creds.get("api_key", "")
    if not key:
        return False, "No API key saved."
    body = json.dumps({"model": creds.get("model") or "gpt-4o-mini", "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]}).encode()
    status, text = _http("https://api.openai.com/v1/chat/completions", {"authorization": f"Bearer {key}", "content-type": "application/json"}, body, "POST")
    if status == 200:
        return True, "The key works: the provider answered a test request."
    return False, f"Provider answered {status}: {text[:160]}"


def _test_ai(creds):
    key = creds.get("api_key", "")
    if not key:
        return False, "No API key saved."
    body = json.dumps({"model": creds.get("model") or settings.AI_MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]}).encode()
    status, text = _http("https://api.anthropic.com/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01",
                                                                    "content-type": "application/json"}, body, "POST")
    if status == 200:
        return True, "The key works: the provider answered a test request."
    try:
        err = json.loads(text).get("error", {})
        msg = err.get("message", text)
    except ValueError:
        msg = text
    if status in (401, 403):
        return False, f"Invalid or unauthorised key ({status}): {msg}"
    if status == 429 or "credit" in msg.lower() or "billing" in msg.lower():
        return False, f"The key is valid but has no quota or billing ({status}): {msg}"
    return False, f"Provider error {status}: {msg}"


def _test_brevo(creds):
    key = creds.get("api_key", "")
    if not key:
        return False, "No API key saved."
    status, text = _http("https://api.brevo.com/v3/account", {"api-key": key, "accept": "application/json"})
    if status == 200:
        try:
            plan = json.loads(text).get("plan", [{}])[0]
            return True, f"The key works. Plan: {plan.get('type', 'unknown')}, credits: {plan.get('credits', '?')}."
        except (ValueError, IndexError, AttributeError):
            return True, "The key works."
    if status in (401, 403):
        return False, f"Invalid or unauthorised Brevo key ({status}). Use a v3 API key from SMTP & API → API keys."
    return False, f"Brevo error {status}: {text[:160]}"


def _test_sms(creds):
    user, key = creds.get("username", ""), creds.get("api_key", "")
    if not user or not key:
        return False, "Username and API key are both required."
    host = "api.sandbox.africastalking.com" if user == "sandbox" else "api.africastalking.com"
    status, text = _http(f"https://{host}/version1/user?username={user}", {"apiKey": key, "Accept": "application/json"})
    if status == 200:
        try:
            bal = json.loads(text).get("UserData", {}).get("balance", "")
            return True, f"The key works. Balance: {bal or 'n/a'}" + (" (sandbox)" if user == "sandbox" else ".")
        except ValueError:
            return True, "The key works."
    if status in (401, 403):
        return False, f"Invalid username or API key ({status})."
    return False, f"Africa's Talking error {status}: {text[:160]}"


def test_credentials(provider: str, user=None, request=None) -> tuple[bool, str]:
    """One real, minimal call to the provider when keys are live; reports the actual error. In mock mode no call is made."""
    row = IntegrationCredential.objects.filter(provider=provider).first()
    creds = {name: get_credential(provider, name) for name, _l, _s in PROVIDERS[provider][1]}
    if row is None and not any(creds.values()):
        ok, msg = False, "No key saved yet."
    elif getattr(settings, "INTEGRATIONS_OFFLINE", False) and provider in ("ai", "openai", "whatsapp") and not os.environ.get("FORCE_LIVE_KEY_TEST"):
        ok, msg = bool(any(creds.values())), "Offline mode: the key is saved but no live call was made."
    elif provider == "ai":
        ok, msg = _test_ai(creds)
    elif provider == "openai":
        ok, msg = _test_openai(creds)
    elif provider == "whatsapp":
        from apps.ict import whatsapp

        rows = whatsapp.check_connection()
        ok = all(r["ok"] for r in rows if r["label"] in ("Phone number", "Business account", "Access token and phone number ID"))
        msg = " · ".join(f"{'✓' if r['ok'] else '✗'} {r['label']}: {r['detail']}" for r in rows)
    elif provider == "brevo":
        ok, msg = _test_brevo(creds)
    elif provider == "sms":
        ok, msg = _test_sms(creds)
    else:
        ok, msg = bool(any(creds.values())), "Saved. A live check isn't available for this provider yet; it is used when INTEGRATIONS_MODE=live."
    if row is not None:
        row.last_test_at, row.last_test_ok, row.last_test_message = timezone.now(), ok, msg[:300]
        row.save(update_fields=["last_test_at", "last_test_ok", "last_test_message"])
    audit(user, "credential.tested", row, f"{'OK' if ok else 'FAILED'}: {msg}"[:500], request, label=PROVIDERS[provider][0])
    return ok, msg
