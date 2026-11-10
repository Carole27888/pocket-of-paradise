from django.core.management.base import BaseCommand

from apps.core.jobs import run_due


class Command(BaseCommand):
    help = "Process due scheduled tasks and queued jobs (run from cron every 5 minutes)."

    def add_arguments(self, parser):
        parser.add_argument("--time-limit", type=int, default=None)

    def handle(self, *args, **opts):
        res = run_due(time_limit=opts["time_limit"])
        if res["locked"]:
            self.stdout.write("Another run_jobs is active; nothing to do.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Scheduled {res['scheduled']}, ran {res['ran']} job(s)."))
