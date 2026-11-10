"""Fleet spending flows into Finance automatically: fuel and maintenance become paid expenses."""
from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.fleet.models import FuelLog, MaintenanceLog


def _vat_part(amount):
    return (Decimal(amount) * Decimal("16") / Decimal("116")).quantize(Decimal("0.01"))


@receiver(post_save, sender=FuelLog)
def fuel_to_expense(sender, instance, created, **kw):
    if not created or not instance.amount:
        return
    from .services import record_expense

    record_expense(None, auto_approve=True, date=instance.date, category="fuel", payee=instance.station or "Fuel station",
                   description=f"{instance.vehicle} · {instance.litres}L", amount=instance.amount,
                   vat_amount=_vat_part(instance.amount), method="mpesa", vehicle=instance.vehicle, source=f"fuel_log:{instance.pk}")


@receiver(post_save, sender=MaintenanceLog)
def maintenance_to_expense(sender, instance, created, **kw):
    if not created or not instance.cost:
        return
    from .services import record_expense

    record_expense(None, auto_approve=True, date=instance.date, category="maintenance", payee=instance.garage or "Garage",
                   description=f"{instance.vehicle} · {instance.description}", amount=instance.cost,
                   vat_amount=_vat_part(instance.cost), method="bank", vehicle=instance.vehicle,
                   source=f"maintenance:{instance.pk}")
