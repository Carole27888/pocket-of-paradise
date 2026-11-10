"""Weekly planning: everyone plans their week, then marks each day's items done, partly done or not done."""
from datetime import timedelta

from django.utils import timezone

from .models import WeeklyPlanItem as P

MARK_AFTER_HOUR = 16   # from 4pm today's unmarked items become a nudge


def week_days(monday):
    return [monday + timedelta(days=i) for i in range(7)]


def week_summary(items):
    n = {k: 0 for k, _ in P.STATUSES}
    for i in items:
        n[i.status] += 1
    total = len(items)
    score = n["done"] + 0.5 * n["partial"]
    marked = total - n["planned"]
    return {"total": total, **n, "marked": marked, "pct": round(100 * score / marked) if marked else None,
            "progress": round(100 * marked / total) if total else 0}


def board(user, monday):
    """The seven days of a week with their items, for the To-Do page."""
    items = list(P.objects.filter(user=user, day__range=(monday, monday + timedelta(days=6))))
    by_day = {}
    for i in items:
        by_day.setdefault(i.day, []).append(i)
    today = timezone.localdate()
    days = [{"date": d, "items": by_day.get(d, []), "today": d == today, "past": d < today, "can_mark": d <= today}
            for d in week_days(monday)]
    return days, week_summary(items)


def nudge(user, now=None):
    """What this person must do now: plan the week, or mark what they did. None when they are up to date."""
    now = now or timezone.localtime()
    today = now.date()
    monday = P.monday(today)
    mine = P.objects.filter(user=user)
    if not mine.filter(day__range=(monday, monday + timedelta(days=6))).exists():
        return {"kind": "plan", "title": "Plan your week", "text": "Add what you will do each day this week, with times. It takes two minutes and your manager can see it.",
                "cta": "Plan my week"}
    unmarked = mine.filter(status=P.PLANNED, day__lt=today).count()
    if now.hour >= MARK_AFTER_HOUR:
        unmarked += mine.filter(status=P.PLANNED, day=today).count()
    if unmarked:
        return {"kind": "mark", "title": "Mark how your day went",
                "text": f"{unmarked} planned item{'s are' if unmarked != 1 else ' is'} not marked yet. Say done, partly done or not done.", "cta": "Mark them now"}
    return None
