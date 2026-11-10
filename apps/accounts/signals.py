from django.contrib.auth.models import Group
from django.db.models.signals import post_migrate
from django.dispatch import receiver

from . import roles


@receiver(post_migrate)
def ensure_role_groups(sender, **kwargs):
    if getattr(sender, "label", "") != "accounts":
        return
    for name in roles.ALL_ROLES:
        Group.objects.get_or_create(name=name)
