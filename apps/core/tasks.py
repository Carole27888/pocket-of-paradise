from datetime import timedelta

from django.utils import timezone

from .jobs import daily, task
from .jobs.models import Job


@daily(hour=3, minute=15)
@task("core.prune_finished_jobs")
def prune_finished_jobs(days: int = 30):
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = Job.objects.filter(status=Job.DONE, finished_at__lt=cutoff).delete()
    return f"pruned {deleted}"


@task("core.noop")
def noop(**kwargs):
    return "ok"
