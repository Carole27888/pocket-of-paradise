"""Every destination gets its photos in one place: a library pack with the same name becomes the destination's photo set, and the
pictures from the older admin gallery are added to it (the files are reused, not copied)."""
from django.db import migrations
from django.utils.text import slugify


def link(apps, schema_editor):
    Destination = apps.get_model("catalog", "Destination")
    PhotoCategory = apps.get_model("catalog", "PhotoCategory")
    LibraryPhoto = apps.get_model("catalog", "LibraryPhoto")
    DestinationPhoto = apps.get_model("catalog", "DestinationPhoto")
    for d in Destination.objects.filter(deleted_at__isnull=True, photo_pack__isnull=True):
        pack = PhotoCategory.objects.filter(kind="destination", name__iexact=d.name, destination__isnull=True).first()
        gallery = list(DestinationPhoto.objects.filter(destination=d).order_by("sort_order", "pk"))
        if pack is None and not gallery:
            continue
        if pack is None:
            name, n = d.name[:90], 2
            while PhotoCategory.objects.filter(name__iexact=name).exists():
                name, n = f"{d.name[:90]} ({n})", n + 1
            slug, n = slugify(name)[:110] or "pack", 2
            while PhotoCategory.objects.filter(slug=slug).exists():
                slug, n = f"{slugify(name)[:110]}-{n}", n + 1
            pack = PhotoCategory.objects.create(name=name, slug=slug, kind="destination", description=f"Photos of {d.name}")
        d.photo_pack = pack
        d.save(update_fields=["photo_pack"])
        have = set(LibraryPhoto.objects.filter(category=pack).values_list("image", flat=True))
        for g in gallery:
            if g.image and g.image.name not in have:
                LibraryPhoto.objects.create(category=pack, image=g.image.name, caption=g.caption[:200])


class Migration(migrations.Migration):
    dependencies = [("catalog", "0005_destination_photo_pack")]
    operations = [migrations.RunPython(link, migrations.RunPython.noop)]
