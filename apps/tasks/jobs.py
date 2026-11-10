from apps.core.jobs import every, task


@every(30)
@task("tasks.escalate_overdue")
def escalate_overdue():
    from .services import escalate_overdue as run

    return f"{run()} escalated"
