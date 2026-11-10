"""Bring the standalone Itinerary Builder's photo library and templates into this system.

    python manage.py import_itinerary_builder "/path/to/ITINERARY BUILDER"

Reads its db.sqlite3 and media folder. Safe to run twice: packs and templates that already exist by name are skipped."""
import sqlite3
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import LibraryPhoto, PhotoCategory
from apps.sales.models import ItineraryTemplate, TemplateDay, TemplatePriceLine


class Command(BaseCommand):
    help = "Import photo packs, photos and itinerary templates from the standalone Itinerary Builder."

    def add_arguments(self, parser):
        parser.add_argument("path", help="The Itinerary Builder folder (holds db.sqlite3 and media/)")

    def handle(self, *args, **opts):
        root = Path(opts["path"]).expanduser()
        db = root / "db.sqlite3"
        if not db.exists():
            raise CommandError(f"No db.sqlite3 in {root}")
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        packs = photos = skipped = 0
        for c in con.execute("select * from itineraries_category order by id"):
            cat, made = PhotoCategory.objects.get_or_create(name=c["name"], defaults={"kind": c["kind"], "description": c["description"] or ""})
            packs += made
            if not made and cat.images.exists():
                continue                                          # already imported
            for im in con.execute("select * from itineraries_categoryimage where category_id=? order by id", (c["id"],)):
                src = root / "media" / im["image"]
                if not src.exists():
                    skipped += 1
                    continue
                photo = LibraryPhoto(category=cat, caption=im["caption"] or "", tags=im["tags"] or "")
                photo.image.save(src.name, ContentFile(src.read_bytes()), save=False)
                photo.save()
                photos += 1
        templates = 0
        for t in con.execute("select * from itineraries_itinerarytemplate order by id"):
            if ItineraryTemplate.objects.filter(title=t["name"]).exists():
                continue
            tpl = ItineraryTemplate.objects.create(
                title=t["name"], description=(t["tagline"] or t["description"] or "")[:300], intro=t["intro"] or "", inclusions=t["inclusions"] or "",
                exclusions=t["exclusions"] or "", terms=t["terms"] or "", nights_override=t["nights"])
            for d in con.execute("select * from itineraries_templateday where template_id=? order by number, id", (t["id"],)):
                TemplateDay.objects.create(template=tpl, day_number=d["number"], title=d["title"][:160], description=d["description"] or "")
            for p in con.execute("select * from itineraries_templatepriceitem where template_id=? order by id", (t["id"],)):
                TemplatePriceLine.objects.create(template=tpl, description=p["description"][:200], quantity=p["quantity"], unit_price=p["unit_price"])
            templates += 1
        self.stdout.write(self.style.SUCCESS(f"Imported {packs} packs, {photos} photos ({skipped} missing files), {templates} templates."))
