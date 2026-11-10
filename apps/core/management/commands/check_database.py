from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = "Show the active database engine/host/name (never the password) and test connectivity."

    def handle(self, *args, **opts):
        for alias in connections:
            cfg = connections[alias].settings_dict
            engine = cfg["ENGINE"].rsplit(".", 1)[-1]
            self.stdout.write(
                f"[{alias}] engine={engine} host={cfg.get('HOST') or '-'} port={cfg.get('PORT') or '-'} "
                f"name={cfg.get('NAME')}"
            )
            try:
                with connections[alias].cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
                vendor_version = getattr(connections[alias], "mysql_server_info", None) or ""
                self.stdout.write(self.style.SUCCESS(f"[{alias}] connection OK {vendor_version}"))
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"[{alias}] connection failed: {exc}") from exc
