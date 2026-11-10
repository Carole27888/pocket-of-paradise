"""Routes read-heavy report/dashboard queries to the replica when DATABASE_REPLICA_URL is set.

Use `Model.objects.using(read_db())` in report services; normal ORM reads/writes stay on default
so a user always reads their own writes.
"""
from django.conf import settings


def read_db() -> str:
    return "replica" if "replica" in settings.DATABASES else "default"


class ReplicaRouter:
    def db_for_read(self, model, **hints):
        return None  # explicit .using(read_db()) only

    def db_for_write(self, model, **hints):
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "default"
