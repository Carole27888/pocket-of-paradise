"""Lodge photo packs in the photo library become the photos of the accommodation with the same name, and builder options that used
such a pack now point at that accommodation. Packs with no matching accommodation are left as they were."""
from django.db import migrations


def link(apps, schema_editor):
    Accommodation = apps.get_model("catalog", "Accommodation")
    PhotoCategory = apps.get_model("catalog", "PhotoCategory")
    ItineraryHotel = apps.get_model("sales", "ItineraryHotel")
    for pack in PhotoCategory.objects.filter(kind="accommodation"):
        acc = Accommodation.objects.filter(name__iexact=pack.name, deleted_at__isnull=True, photo_pack__isnull=True).first()
        if acc is None:
            continue
        acc.photo_pack = pack
        acc.save(update_fields=["photo_pack"])
        ItineraryHotel.objects.filter(photo_pack=pack, accommodation__isnull=True).update(accommodation=acc)


class Migration(migrations.Migration):
    dependencies = [("sales", "0019_itineraryhotel_accommodation_and_more"), ("catalog", "0003_accommodation_photo_pack")]
    operations = [migrations.RunPython(link, migrations.RunPython.noop)]
