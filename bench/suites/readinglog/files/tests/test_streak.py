from datetime import date

from readinglog.models import Session
from readinglog.streak import current_streak, pages_this_week

SAT = date(2026, 10, 10)


def test_streak_counts_consecutive_days_back_from_today():
    sessions = [Session("Dune", date(2026, 10, d), 10) for d in (8, 9, 10)]
    assert current_streak(sessions, SAT) == 3


def test_gap_ends_streak():
    sessions = [Session("Dune", date(2026, 10, d), 10) for d in (7, 9, 10)]
    assert current_streak(sessions, SAT) == 2


def test_week_starts_on_monday():
    sessions = [Session("Dune", date(2026, 10, 4), 99), Session("Dune", date(2026, 10, 5), 20)]
    assert pages_this_week(sessions, SAT) == 20
