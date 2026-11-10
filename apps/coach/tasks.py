from apps.core.jobs import every, task


@every(10)
@task("coach.cycle")
def cycle():
    """Every ten minutes: nudges, plus whichever scheduled message (briefing, end of day, weekly, digest, executive) has come due. Each goes out once a day."""
    from . import services

    n = services.run_nudges()
    n += services.send_morning_briefings() + services.send_end_of_day() + services.send_weekly() + services.send_digest() + services.send_exec_briefing()
    return f"{n} messages"
