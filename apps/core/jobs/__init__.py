"""Database-backed job system (section 1.4).

    from apps.core.jobs import enqueue, task, every, daily, monthly

    @task("reports.send_pack")
    def send_pack(report_id): ...

    enqueue("reports.send_pack", report_id=3)                 # inline with JOBS_BACKEND=sync
    enqueue("reports.send_pack", report_id=3, run_at=later)   # always stored, run by run_jobs

    @every(minutes=5)
    def check_late_returns(): ...
"""
from .registry import SCHEDULES, TASKS, daily, every, monthly, task  # noqa: F401
from .runner import enqueue, execute_job, run_due  # noqa: F401
