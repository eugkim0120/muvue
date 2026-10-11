from __future__ import annotations

from datetime import date

from readinglog import store
from readinglog.streak import current_streak, pages_this_week


def register(sub) -> None:
    parser = sub.add_parser("stats", help="pages this week and the current streak")
    parser.set_defaults(func=run)


def run(args) -> int:
    _, sessions = store.load()
    today = date.today()
    streak = current_streak(sessions, today)
    print(f"Pages this week: {pages_this_week(sessions, today)}")
    print(f"Current streak: {streak} day{'' if streak == 1 else 's'}")
    return 0
