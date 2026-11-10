from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from apps.pwa.devices import splash_images

register = template.Library()


@register.simple_tag
def pwa_head():
    theme = settings.PWA_THEME_COLOR
    head = [
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
        format_html('<link rel="manifest" href="/manifest.webmanifest?v={}">', settings.PWA_VERSION),
        format_html('<meta name="theme-color" content="{}">', theme),
        '<meta name="mobile-web-app-capable" content="yes">',
        '<meta name="apple-mobile-web-app-capable" content="yes">',
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">',
        '<meta name="apple-mobile-web-app-title" content="Pocket of Paradise">',
        '<meta name="application-name" content="Paradise">',
        format_html('<link rel="apple-touch-icon" href="{}">', static("pwa/apple-touch-icon.png")),
        format_html('<link rel="icon" type="image/png" sizes="32x32" href="{}">', static("pwa/favicon-32.png")),
        format_html('<link rel="icon" href="{}" sizes="any">', static("pwa/favicon.ico")),
    ]
    splash = format_html_join(
        "\n",
        '<link rel="apple-touch-startup-image" media="(device-width: {}px) and (device-height: {}px) and '
        '(-webkit-device-pixel-ratio: {}) and (orientation: {})" href="{}">',
        ((s["w"], s["h"], s["r"], s["orientation"], static(s["file"])) for s in splash_images()),
    )
    return mark_safe("\n".join(str(h) for h in head) + "\n" + splash)
