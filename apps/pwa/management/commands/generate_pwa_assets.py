"""Generate every PWA icon, favicon, iOS splash screen and store screenshot placeholder with Pillow.

    python manage.py generate_pwa_assets                 # from static/brand/logo-source.png
    python manage.py generate_pwa_assets --screenshots path/to/dir   # use real wide.png + narrow.png

Re-run only when the logo changes, then bump PWA_VERSION.
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw, ImageFont

from apps.pwa.devices import ICON_SIZES, MASKABLE_SIZES, splash_images

BRAND = (31, 111, 107)        # #1F6F6B deep sea teal (the logo's sea, darkened for contrast)
CREAM = (249, 237, 215)       # #F9EDD7 the logo's background
INK = (60, 45, 24)            # #3C2D18 the logo's outline and wordmark
SUN = (237, 160, 54)          # #EDA036 the logo's sun
PEACH = (238, 182, 63)        # #EEB63F the logo's sand


def static_dir() -> Path:
    return Path(settings.BASE_DIR) / "static"


def font(name: str, size: int):
    path = static_dir() / "fonts" / name
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default(size=size)


def fit(logo: Image.Image, box: int) -> Image.Image:
    img = logo.copy()
    img.thumbnail((box, box), Image.LANCZOS)
    return img


def on_background(logo, size, scale, bg):
    canvas = Image.new("RGBA", (size, size), bg + (255,))
    mark = fit(logo, int(size * scale))
    canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return canvas


class Command(BaseCommand):
    help = "Generate PWA icons, favicons, iOS splash screens and manifest screenshots from the brand logo."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(static_dir() / "brand" / "logo-source.png"))
        parser.add_argument("--screenshots", default="", help="Folder containing real wide.png and narrow.png")

    def handle(self, *args, **opts):
        src = Path(opts["source"])
        if not src.exists():
            raise CommandError(f"Logo not found: {src} (needs ≥1024×1024 PNG, transparent background)")
        logo = Image.open(src).convert("RGBA")
        bbox = logo.getbbox()
        if bbox:
            logo = logo.crop(bbox)
        if max(logo.size) < 512:
            self.stderr.write(self.style.WARNING("Logo is small; icons may look soft. Use at least 1024×1024."))
        out = static_dir() / "pwa"
        (out / "splash").mkdir(parents=True, exist_ok=True)
        n = 0
        for s in ICON_SIZES:  # "any": mark on transparent background
            canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            m = fit(logo, int(s * 0.96))
            canvas.alpha_composite(m, ((s - m.width) // 2, (s - m.height) // 2))
            canvas.save(out / f"icon-{s}.png", optimize=True)
            n += 1
        for s in MASKABLE_SIZES:  # logo inside the 80% safe zone
            on_background(logo, s, 0.80, CREAM).save(out / f"maskable-{s}.png", optimize=True)
            n += 1
        on_background(logo, 180, 0.96, CREAM).convert("RGB").save(out / "apple-touch-icon.png", optimize=True)
        fav = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        fm = fit(logo, 32)
        fav.alpha_composite(fm, ((32 - fm.width) // 2, (32 - fm.height) // 2))
        fav.save(out / "favicon-32.png")
        big = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        bm = fit(logo, 64)
        big.alpha_composite(bm, ((64 - bm.width) // 2, (64 - bm.height) // 2))
        big.save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
        for s in (48, 96):
            m = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            mm = fit(logo, s)
            m.alpha_composite(mm, ((s - mm.width) // 2, (s - mm.height) // 2))
            m.save(out / f"mark-{s}.png", optimize=True)
        n += 5
        for sp in splash_images():
            self.splash(logo, sp["px_w"], sp["px_h"]).save(static_dir() / sp["file"], optimize=True)
            n += 1
        shots = Path(opts["screenshots"]) if opts["screenshots"] else None
        if shots and (shots / "wide.png").exists() and (shots / "narrow.png").exists():
            Image.open(shots / "wide.png").convert("RGB").resize((1280, 800)).save(out / "screenshot-wide.png")
            Image.open(shots / "narrow.png").convert("RGB").resize((750, 1334)).save(out / "screenshot-narrow.png")
        else:
            self.screenshot(logo, 1280, 800).save(out / "screenshot-wide.png", optimize=True)
            self.screenshot(logo, 750, 1334).save(out / "screenshot-narrow.png", optimize=True)
        n += 2
        full = static_dir() / "brand" / "logo-full.png"
        if not full.exists():
            shutil.copy(src, full)
        self.stdout.write(self.style.SUCCESS(f"Generated {n} files in {out}. Bump PWA_VERSION and redeploy."))

    # ------------------------------------------------------------------ drawing
    def splash(self, logo, w, h):
        img = Image.new("RGB", (w, h), CREAM)
        d = ImageDraw.Draw(img)
        short = min(w, h)
        landscape = w > h
        mark = fit(logo, int(short * (0.34 if landscape else 0.42)))
        title_size = int(short * (0.058 if landscape else 0.068))
        tag_size = int(title_size * 0.46)
        f_title = font("fraunces-latin-800-normal.woff2", title_size)
        f_tag = font("fraunces-latin-500-italic.woff2", tag_size)
        lines = ["POCKET OF", "PARADISE"]
        line_h = int(title_size * 1.12)
        block = mark.height + int(short * 0.05) + line_h * 2 + int(tag_size * 1.9)
        y = (h - block) // 2
        img.paste(mark, ((w - mark.width) // 2, y), mark)
        y += mark.height + int(short * 0.05)
        for line in lines:
            tw = d.textlength(line, font=f_title)
            d.text(((w - tw) / 2, y), line, font=f_title, fill=INK)
            y += line_h
        tag = settings.COMPANY_TAGLINE
        tw = d.textlength(tag, font=f_tag)
        d.text(((w - tw) / 2, y + int(tag_size * 0.5)), tag, font=f_tag, fill=BRAND)
        return img

    def screenshot(self, logo, w, h):
        """Branded preview used until real screenshots are supplied with --screenshots."""
        img = Image.new("RGB", (w, h), (251, 247, 240))
        d = ImageDraw.Draw(img)
        f_h = font("manrope-latin-800-normal.woff2", 30 if w > h else 36)
        f_b = font("manrope-latin-600-normal.woff2", 16 if w > h else 22)
        f_n = font("manrope-latin-800-normal.woff2", 30 if w > h else 40)
        if w > h:
            d.rectangle((0, 0, 248, h), fill=(46, 33, 20))
            m = fit(logo, 44)
            img.paste(m, (20, 18), m)
            d.text((72, 24), "POCKET OF PARADISE", font=f_b, fill=CREAM)
            for i, t in enumerate(["Operations Dashboard", "Enquiries", "Clients", "Destinations", "Tour Packages",
                                   "Fleet", "Staff & Guides"]):
                y = 110 + i * 48
                if i == 0:
                    d.rounded_rectangle((12, y - 10, 236, y + 30), 8, fill=(31, 111, 107))
                d.text((28, y), t, font=f_b, fill=CREAM)
            x0, y0, cw = 276, 90, (w - 276 - 28 - 48) // 4
        else:
            d.rectangle((0, 0, w, 120), fill=(46, 33, 20))
            m = fit(logo, 72)
            img.paste(m, (24, 24), m)
            d.text((110, 44), "POCKET OF PARADISE", font=f_b, fill=CREAM)
            x0, y0, cw = 24, 150, (w - 48 - 16) // 2
        d.text((x0, y0 - 60 if w > h else y0), "Operations Dashboard", font=f_h, fill=(35, 24, 21))
        y0 += 0 if w > h else 70
        kpis = [("Open enquiries", "184"), ("Clients", "1,246"), ("Vehicles free", "5 / 8"), ("Packages", "32")]
        cols = 4 if w > h else 2
        for i, (lab, val) in enumerate(kpis):
            cx = x0 + (i % cols) * (cw + 16)
            cy = y0 + (i // cols) * 150
            d.rounded_rectangle((cx, cy, cx + cw, cy + 130), 14, fill="white", outline=(237, 227, 211))
            d.text((cx + 18, cy + 18), lab, font=f_b, fill=(110, 98, 91))
            d.text((cx + 18, cy + 56), val, font=f_n, fill=(35, 24, 21))
        cy = y0 + (1 if w > h else 2) * 150 + 10
        d.rounded_rectangle((x0, cy, w - 28 if w > h else w - 24, h - 28), 14, fill="white", outline=(237, 227, 211))
        bars = [42, 58, 35, 66, 51, 74, 62, 80, 55, 70]
        bw = ((w - 28 if w > h else w - 24) - x0 - 60) // len(bars)
        base = h - 60
        for i, b in enumerate(bars):
            bx = x0 + 30 + i * bw
            top = base - int(b / 80 * (base - cy - 60))
            d.rounded_rectangle((bx, top, bx + bw - 14, base), 6, fill=(237, 160, 54) if i % 2 else (31, 111, 107))
        return img
