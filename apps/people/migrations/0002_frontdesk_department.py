from django.db import migrations


def add_department(apps, schema_editor):
    Department = apps.get_model("people", "Department")
    Department.objects.get_or_create(
        name="Front Desk",
        defaults={"description": "Reception, reservations, enquiry capture and trip dispatch"})


class Migration(migrations.Migration):
    dependencies = [("people", "0001_initial")]
    operations = [migrations.RunPython(add_department, migrations.RunPython.noop)]
