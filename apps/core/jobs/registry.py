"""Task + schedule registry for the database-backed job system."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from django.utils import timezone

TASKS: dict[str, Callable] = {}
SCHEDULES: dict[str, "Schedule"] = {}


def task(name: str | None = None):
    """Register a function as a background task: @task("leads.check_response")."""

    def deco(fn):
        TASKS[name or f"{fn.__module__}.{fn.__name__}"] = fn
        fn.task_name = name or f"{fn.__module__}.{fn.__name__}"
        return fn

    return deco


@dataclass
class Schedule:
    name: str
    task_name: str
    kind: str  # interval | daily | monthly
    minutes: int = 0
    hour: int = 0
    minute: int = 0
    day: int = 1
    kwargs: dict = field(default_factory=dict)

    def next_after(self, when: datetime) -> datetime:
        local = timezone.localtime(when)
        if self.kind == "interval":
            return when + timedelta(minutes=self.minutes)
        if self.kind == "daily":
            cand = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
            if cand <= local:
                cand += timedelta(days=1)
            return cand
        if self.kind == "monthly":
            cand = local.replace(day=self.day, hour=self.hour, minute=self.minute, second=0, microsecond=0)
            if cand <= local:
                month = local.month + 1
                year = local.year + (month > 12)
                month = 1 if month > 12 else month
                cand = cand.replace(year=year, month=month)
            return cand
        raise ValueError(self.kind)


def _register_schedule(kind, fn, **opts):
    tname = getattr(fn, "task_name", None) or f"{fn.__module__}.{fn.__name__}"
    TASKS.setdefault(tname, fn)
    fn.task_name = tname
    SCHEDULES[tname] = Schedule(name=tname, task_name=tname, kind=kind, **opts)
    return fn


def every(minutes: int):
    return lambda fn: _register_schedule("interval", fn, minutes=minutes)


def daily(hour: int = 2, minute: int = 0):
    return lambda fn: _register_schedule("daily", fn, hour=hour, minute=minute)


def monthly(day: int = 1, hour: int = 6, minute: int = 0):
    return lambda fn: _register_schedule("monthly", fn, day=day, hour=hour, minute=minute)
