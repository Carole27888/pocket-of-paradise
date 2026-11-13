from apps.core.jobs import daily, task


@daily(hour=6, minute=30)
@task("insights.check_competitors")
def check_competitors():
    """Every morning: check each competitor's website pages and Google profile, and alert the watchers about changes."""
    from .competitors import check_all

    return f"{check_all()} changes"
