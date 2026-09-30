"""Calendar View — a month-grid of scheduled exams, for both the admin
side (every test in the org with a start_time) and the student side
(every test they're eligible for with a start_time). Deliberately just a
grid over Test.start_time — no separate "event" concept, no recurrence,
no drag-to-reschedule; it's a read-only view onto data that already
exists, not a new scheduling system. Tests without a start_time set
aren't tied to a specific day and so don't appear on the grid at all
(the existing list views already show those).
"""
import calendar as calendar_module
from datetime import date


def month_bounds(year, month):
    """(first_day, last_day) date objects for year/month — used to scope
    the DB query to exactly the days the grid will actually render."""
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar_module.monthrange(year, month)[1])
    return first_day, last_day


def prev_next_month(year, month):
    """(prev_year, prev_month, next_year, next_month) for the grid's
    navigation links — handles the year rollover at January/December."""
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return prev_year, prev_month, next_year, next_month


def build_month_grid(year, month, tests):
    """tests: an iterable of Test rows with a non-null start_time,
    already scoped to this month by the caller's query. Returns a list of
    weeks (Sunday-first), each a list of day dicts with 'date',
    'in_month', and 'tests' (sorted by start_time within the day)."""
    by_day = {}
    for test in tests:
        day = test.start_time.date()
        by_day.setdefault(day, []).append(test)
    for day_tests in by_day.values():
        day_tests.sort(key=lambda t: t.start_time)

    cal = calendar_module.Calendar(firstweekday=6)  # Sunday-first
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        row = [
            {"date": day, "in_month": day.month == month, "tests": by_day.get(day, [])}
            for day in week
        ]
        weeks.append(row)
    return weeks
