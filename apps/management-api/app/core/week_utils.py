from datetime import date, timedelta


def resolve_week(week: date | None) -> tuple[date, date]:
    if week is None:
        week = date.today()
    monday = week - timedelta(days=week.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def week_label(monday: date, sunday: date) -> str:
    return f"{monday.day:02d}-to-{sunday.day:02d}"


def month_dir(monday: date) -> str:
    return monday.strftime("%m-%Y")


def is_current_week(monday: date) -> bool:
    current_monday, _ = resolve_week(date.today())
    return monday == current_monday
