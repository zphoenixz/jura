from datetime import date

from app.core.week_utils import month_dir, resolve_week, week_label


def test_resolve_week_monday():
    monday, sunday = resolve_week(date(2026, 3, 30))
    assert monday == date(2026, 3, 30)
    assert sunday == date(2026, 4, 5)


def test_resolve_week_wednesday():
    monday, sunday = resolve_week(date(2026, 4, 1))
    assert monday == date(2026, 3, 30)
    assert sunday == date(2026, 4, 5)


def test_resolve_week_sunday_snaps_to_previous_monday():
    monday, sunday = resolve_week(date(2026, 3, 29))
    assert monday == date(2026, 3, 23)
    assert sunday == date(2026, 3, 29)


def test_resolve_week_none_returns_current():
    monday, sunday = resolve_week(None)
    assert monday.weekday() == 0
    assert (sunday - monday).days == 6


def test_week_label():
    assert week_label(date(2026, 3, 30), date(2026, 4, 5)) == "30-to-05"
    assert week_label(date(2026, 3, 2), date(2026, 3, 8)) == "02-to-08"


def test_month_dir():
    assert month_dir(date(2026, 3, 30)) == "03-2026"
    assert month_dir(date(2026, 12, 1)) == "12-2026"
