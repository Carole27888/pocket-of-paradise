import logging
import time
import traceback
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Job, JobLock, ScheduleState
from .registry import SCHEDULES, TASKS

log = logging.getLogger("jobs")
RUNNER_LOCK = "run_jobs"


def enqueue(task_name: str, run_at=None, **kwargs) -> Job:
    if task_name not in TASKS:
        raise KeyError(f"Unknown task {task_name!r}")
    backend = getattr(settings, "JOBS_BACKEND", "sync")
    job = Job.objects.create(task_name=task_name, kwargs=kwargs, run_at=run_at or timezone.now())
    if backend == "sync" and (run_at is None or run_at <= timezone.now()):
        execute_job(job)
    elif backend == "celery":  # optional adapter, only if the project adds Celery later
        from .celery_adapter import send  # pragma: no cover

        send(job)  # pragma: no cover
    return job


def execute_job(job: Job) -> Job:
    fn = TASKS.get(job.task_name)
    job.attempts += 1
    job.status = Job.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["attempts", "status", "started_at"])
    try:
        if fn is None:
            raise KeyError(f"Task {job.task_name} is not registered")
        result = fn(**(job.kwargs or {}))
        job.status = Job.DONE
        job.result = "" if result is None else str(result)[:2000]
        job.last_error = ""
        log.info("job %s %s done", job.pk, job.task_name)
    except Exception:  # noqa: BLE001 - job failures are recorded, never raised
        job.last_error = traceback.format_exc()[-4000:]
        if job.attempts >= settings.JOBS_MAX_ATTEMPTS:
            job.status = Job.FAILED
            log.error("job %s %s failed permanently", job.pk, job.task_name)
        else:
            job.status = Job.QUEUED
            job.run_at = timezone.now() + timedelta(minutes=2 ** job.attempts)  # 2, 4 min backoff
            log.warning("job %s %s failed, retry at %s", job.pk, job.task_name, job.run_at)
    job.finished_at = timezone.now()
    job.save()
    return job


def acquire_lock(name: str = RUNNER_LOCK, ttl: int | None = None) -> str | None:
    ttl = ttl or settings.JOBS_RUN_TIME_LIMIT + 60
    now = timezone.now()
    owner = uuid.uuid4().hex
    JobLock.objects.get_or_create(name=name, defaults={"locked_until": now - timedelta(seconds=1)})
    got = JobLock.objects.filter(name=name, locked_until__lte=now).update(
        owner=owner, locked_until=now + timedelta(seconds=ttl), last_heartbeat=now
    )
    return owner if got else None


def release_lock(owner: str, name: str = RUNNER_LOCK):
    JobLock.objects.filter(name=name, owner=owner).update(locked_until=timezone.now())


def enqueue_due_schedules(now=None) -> int:
    now = now or timezone.now()
    count = 0
    for name, sched in SCHEDULES.items():
        state, created = ScheduleState.objects.get_or_create(name=name)
        if state.next_run_at is None:
            # interval tasks run on first pass; daily/monthly wait for their slot
            state.next_run_at = now if sched.kind == "interval" else sched.next_after(now)
            state.save()
        if state.next_run_at <= now:
            with transaction.atomic():
                Job.objects.create(task_name=sched.task_name, kwargs=sched.kwargs, schedule_name=name, run_at=now)
                state.last_run_at = now
                state.next_run_at = sched.next_after(now)
                state.save()
            count += 1
    return count


def run_due(time_limit: int | None = None) -> dict:
    """One runner pass: schedules → queued jobs, under a DB lock and a time limit."""
    owner = acquire_lock()
    if not owner:
        log.info("run_jobs: another runner holds the lock, exiting")
        return {"locked": True, "scheduled": 0, "ran": 0}
    started = time.monotonic()
    limit = time_limit or settings.JOBS_RUN_TIME_LIMIT
    ran = 0
    try:
        scheduled = enqueue_due_schedules()
        while time.monotonic() - started < limit:
            job = (
                Job.objects.filter(status=Job.QUEUED, run_at__lte=timezone.now()).order_by("run_at", "pk").first()
            )
            if job is None:
                break
            execute_job(job)
            ran += 1
        JobLock.objects.filter(name=RUNNER_LOCK).update(last_heartbeat=timezone.now())
        return {"locked": False, "scheduled": scheduled, "ran": ran}
    finally:
        release_lock(owner)


def last_heartbeat():
    lock = JobLock.objects.filter(name=RUNNER_LOCK).first()
    return lock.last_heartbeat if lock else None
