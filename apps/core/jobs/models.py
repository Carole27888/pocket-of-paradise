from django.db import models
from django.utils import timezone


class Job(models.Model):
    QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
    STATUS_CHOICES = [(QUEUED, "Queued"), (RUNNING, "Running"), (DONE, "Done"), (FAILED, "Failed")]

    task_name = models.CharField(max_length=120, db_index=True)
    kwargs = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    run_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    result = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    schedule_name = models.CharField(max_length=120, blank=True)

    class Meta:
        app_label = "core"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "run_at"])]

    def __str__(self):
        return f"{self.task_name} [{self.status}]"


class ScheduleState(models.Model):
    name = models.CharField(max_length=120, unique=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "core"

    def __str__(self):
        return self.name


class JobLock(models.Model):
    """Portable database lock: acquired by a conditional UPDATE, works on SQLite/PostgreSQL/MySQL."""

    name = models.CharField(max_length=60, unique=True)
    owner = models.CharField(max_length=64, blank=True)
    locked_until = models.DateTimeField(default=timezone.now)
    last_heartbeat = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "core"

    def __str__(self):
        return self.name
