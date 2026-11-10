"""Take the real screenshots used in the user manuals from a running copy of the system (development tool; needs Playwright).

    python manage.py runserver 127.0.0.1:8765 &
    python manage.py capture_manual_shots --base http://127.0.0.1:8765
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.manuals.content import MANUALS


class Command(BaseCommand):
    help = "Capture the manual screenshots with a headless browser"

    def add_arguments(self, parser):
        parser.add_argument("--base", default="http://127.0.0.1:8000")
        parser.add_argument("--password", default="Safari@2025")
        parser.add_argument("--manual", default="")

    def handle(self, *a, base, password, manual, **o):
        from playwright.sync_api import sync_playwright

        n = 0
        with sync_playwright() as p:
            b = p.chromium.launch()
            for m in MANUALS:
                if (manual and m.slug != manual) or not m.shot_user:
                    continue
                ctx = b.new_context(service_workers="block", viewport={"width": 1440, "height": 900})
                pg = ctx.new_page()
                pg.goto(f"{base}/accounts/login/")
                pg.fill("#id_email", f"{m.shot_user}@pocketofparadise.co.ke")
                pg.fill("#id_password", password)
                pg.press("#id_password", "Enter")
                pg.wait_for_url(f"{base}/")
                out = settings.BASE_DIR / "static" / "img" / "manual" / m.slug
                out.mkdir(parents=True, exist_ok=True)
                for sec in m.all_sections():
                    if sec.shot and sec.shot_path:
                        pg.goto(base + sec.shot_path)
                        pg.wait_for_timeout(900)
                        pg.screenshot(path=str(out / f"{sec.shot}.png"))
                        n += 1
                ctx.close()
            b.close()
        self.stdout.write(self.style.SUCCESS(f"Captured {n} screenshots"))
