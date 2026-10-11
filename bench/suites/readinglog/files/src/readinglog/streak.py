"""Reading streak and weekly totals, computed from sessions and a given day."""

from __future__ import annotations

from datetime import date, timedelta

from readinglog.models import Session


def current_streak(sessions: list[Session], today: date) -> int:
    days = {s.day for s in sessions if s.pages > 0}
    streak, day = 0, today
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def pages_this_week(sessions: list[Session], today: date) -> int:
    monday = today - timedelta(days=today.weekday())
    return sum(s.pages for s in sessions if monday <= s.day <= today)
