"""Template helpers loaded as builtins: money, phone, pills, querystring, dates."""
from functools import lru_cache

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from apps.core.money import compact_money, format_money
from apps.core.phone import display_phone, whatsapp_link

register = template.Library()


@register.filter
def money(value, currency="KES"):
    return format_money(value, currency or "KES")


@register.filter
def money_compact(value, currency="KES"):
    return compact_money(value, currency or "KES")


@register.filter
def phone(value):
    return display_phone(value or "")


@register.filter
def wa_link(value):
    return whatsapp_link(value or "")


@register.filter
def ds_date(value):
    if not value:
        return "—"
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        value = timezone.localtime(value)
    return f"{value.day} {value:%b %Y}"


@register.filter
def ds_time(value):
    if not value:
        return "—"
    value = timezone.localtime(value)
    return value.strftime("%I:%M %p").lstrip("0")


@register.filter
def ds_datetime(value):
    if not value:
        return "—"
    return f"{ds_date(value)}, {ds_time(value)}"


@register.filter
def duration_hm(seconds):
    if seconds in (None, ""):
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def pct(part, whole):
    try:
        return round(100 * float(part) / float(whole)) if float(whole) else 0
    except (TypeError, ValueError):
        return 0


@register.simple_tag
def stage_pill(enquiry_or_stage, label=None):
    stage = getattr(enquiry_or_stage, "stage", enquiry_or_stage)
    if label is None:
        label = enquiry_or_stage.get_stage_display() if hasattr(enquiry_or_stage, "get_stage_display") else stage
    return format_html('<span class="pill stage-{}">{}</span>', stage, label)


@register.simple_tag
def status_pill(label, tone="neutral"):
    return format_html('<span class="pill pill-{}">{}</span>', tone, label)


@lru_cache(maxsize=None)
def _channel_svg(code: str) -> str:
    from django.template.loader import render_to_string

    inner = render_to_string("components/channel_icon.html", {"code": code})
    return (f'<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true" '
            f'focusable="false">{inner}</svg>')


# Real, filled brand marks for the social channels (24x24). The wrapper supplies the background where the logo has one.
BRAND = {
    "whatsapp": ("brand-wa", '<path fill="#fff" d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z" transform="translate(3.6 3.6) scale(.7)"/>'),
    "instagram": ("brand-ig", '<g fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4.5" y="4.5" width="15" height="15" rx="4.5"/><circle cx="12" cy="12" r="3.6"/></g><circle cx="16.6" cy="7.4" r="1.15" fill="#fff"/>'),
    "tiktok": ("brand-tt", '<g transform="translate(4.6 4.2) scale(.62)"><path fill="#25F4EE" transform="translate(-.9 -.9)" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/><path fill="#FE2C55" transform="translate(.9 .9)" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/><path fill="#fff" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.07-.14 1.61.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z"/></g>'),
    "google": ("brand-go", '<path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>'),
    "facebook": ("brand-fb", '<path fill="#fff" d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z" transform="translate(2.4 2.4) scale(.8)"/>'),
    "youtube": ("brand-yt", '<path fill="#fff" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" transform="translate(2.4 2.4) scale(.8)"/>'),
}


# logo tiles for the integration cards in ICT -> API Keys & Bot (brand marks reuse BRAND above)
PROVIDER_ICONS = {
    "ai": ("brand-claude", '<g stroke="#fff" stroke-width="2.2" stroke-linecap="round"><path d="M12 3.5v17M3.5 12h17M6 6l12 12M18 6 6 18"/></g>'),
    "openai": ("brand-openai", '<g fill="none" stroke="#fff" stroke-width="1.7" stroke-linejoin="round"><path d="M12 3.5l7.4 4.25v8.5L12 20.5l-7.4-4.25v-8.5z"/><path d="M12 3.5V12l7.4 4.25M12 12 4.6 16.25M12 12l7.4-4.25"/></g>'),
    "sms": ("brand-sms", '<g fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"><path d="M4 5.5h16v11H9.5L5 20.5v-4H4z"/></g><circle cx="8.5" cy="11" r="1" fill="#fff"/><circle cx="12" cy="11" r="1" fill="#fff"/><circle cx="15.5" cy="11" r="1" fill="#fff"/>'),
    "brevo": ("brand-brevo", '<g fill="none" stroke="#fff" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"><rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7.5 8 6 8-6"/></g>'),
    "webhooks": ("brand-hook", '<g fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.2 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.2-3.5-8.5s1.1-6.1 3.5-8.5z"/></g>'),
    "mpesa": ("brand-mpesa", '<text x="12" y="16.6" text-anchor="middle" font-size="12.5" font-weight="800" font-family="Arial, sans-serif" fill="#fff">M</text>'),
}
PROVIDER_ALIAS = {"meta": "facebook", "google_ads": "google", "whatsapp": "whatsapp", "tiktok": "tiktok", "youtube": "youtube"}


@register.simple_tag
def provider_icon(key):
    """The logo tile for an integration (Claude, OpenAI, WhatsApp, Meta, TikTok, Google Ads, YouTube, SMS, e-mail, M-PESA, website hook)."""
    cls, body = PROVIDER_ICONS.get(key) or BRAND.get(PROVIDER_ALIAS.get(key, key)) or ("brand-hook", "")
    return format_html('<span class="chan chan-{} chan-brand prov-icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false">{}</svg></span>', cls, mark_safe(body))  # noqa: S308 - static markup


@register.simple_tag
def source_badge(enquiry_or_code, show_label=False):
    """Chip with the channel's real logo (WhatsApp, Instagram, TikTok, Google, Facebook, YouTube) or a coloured glyph for the rest."""
    from apps.leads.models import SOURCE_BADGES, custom_sources, source_labels

    code = getattr(enquiry_or_code, "source", enquiry_or_code)
    _short, tone = SOURCE_BADGES.get(code, ("??", "muted"))
    if code not in SOURCE_BADGES:
        c = next((c for c in custom_sources(False) if c.code == code), None)
        if c:
            _short = c.short
    label = source_labels().get(code, code)
    if code in BRAND:
        cls, body = BRAND[code]
        icon = mark_safe(f'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{body}</svg>')  # noqa: S308 - static markup
        tone = cls
        brand = " chan-brand"
    elif code not in SOURCE_BADGES and _short != "??":
        # a source the COO added (an expo, a roadshow): its own initials on a colour of its own, so each reads at a glance
        palette = ["#B4531A", "#1F7A5C", "#7A3FB5", "#B02A5B", "#1F5FBF", "#8A6D00", "#0E7C86", "#2A8580"]
        bg = palette[sum(map(ord, code)) % len(palette)]
        size = "9.5px" if len(_short) >= 3 else "11px"
        label_html = format_html('<span class="chan chan-custom" style="background:{};font-size:{}" aria-hidden="true">{}</span>', bg, size, _short)
        if show_label:
            return format_html('<span class="src">{}{}</span>', label_html, label)
        return format_html('<span class="chan chan-custom" style="background:{};font-size:{}" title="{}" role="img" aria-label="{}">{}</span>',
                           bg, size, label, label, _short)
    else:
        icon = mark_safe(_channel_svg(code))  # noqa: S308 - static, app-owned markup
        brand = ""
    if show_label:
        return format_html('<span class="src"><span class="chan chan-{}{}" aria-hidden="true">{}</span>{}</span>',
                           tone, brand, icon, label)
    return format_html('<span class="chan chan-{}{}" title="{}" role="img" aria-label="{}">{}</span>',
                       tone, brand, label, label, icon)


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Rebuild the current querystring with overrides: {% qs page=2 %}  (value None removes a key)."""
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    s = q.urlencode()
    return f"?{s}" if s else "?"


@register.simple_tag(takes_context=True)
def sort_link(context, field, label):
    req = context["request"]
    cur = req.GET.get("sort", "")
    nxt = f"-{field}" if cur == field else field
    arrow = " ↑" if cur == field else (" ↓" if cur == f"-{field}" else "")
    q = req.GET.copy()
    q["sort"] = nxt
    q.pop("page", None)
    return format_html('<a class="th-sort" href="?{}" hx-get="?{}" hx-target="#list-region" hx-push-url="true">{}{}</a>',
                       q.urlencode(), q.urlencode(), label, arrow)
