"""Hours worked, for the dashboard.

Read from the time logs. A log covers a date range but carries one total, so
it counts on the day it ended - the rule "Logged this month" on the Projects
page and billing already use (ADR-0015). Weeks start on Monday. Time still on
a timer is not hours worked until it is logged.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func

from .models import Project, TimeLog, db


def _hours_ending_between(first, last):
    """Logged hours whose end day falls in [first, last], both dates inclusive."""
    start = datetime.combine(first, datetime.min.time())
    stop = datetime.combine(last + timedelta(days=1), datetime.min.time())
    total = (db.session.query(func.coalesce(func.sum(TimeLog.hours), 0.0))
             .filter(TimeLog.ended_on >= start, TimeLog.ended_on < stop)
             .scalar())
    return float(total)


def _day(d):
    return f'{d:%a} {d.day} {d:%b}'


def _span(first, last, weekday=False):
    """'3 – 9 Nov', or '26 Oct – 1 Nov' when the month changes."""
    left = f'{first:%a} {first.day}' if weekday else str(first.day)
    right = _day(last) if weekday else f'{last.day} {last:%b}'
    if first.month != last.month:
        left += f' {first:%b}'
    return f'{left} – {right}'


def hours_worked(today):
    """The dashboard's five figures, in display order. Each is a dict with
    `key`, `label`, `hours` and a `note` naming the dates it covers."""
    yesterday = today - timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    last_monday, last_sunday = monday - timedelta(days=7), monday - timedelta(days=1)
    first_of_month = today.replace(day=1)
    last_of_prev = first_of_month - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)

    active_total = float(
        db.session.query(func.coalesce(func.sum(TimeLog.hours), 0.0))
        .join(Project, TimeLog.project_id == Project.id)
        .filter(Project.status == 'active')
        .scalar())
    active_count = Project.query.filter_by(status='active').count()

    return [
        dict(key='previous_day', label='Previous day',
             hours=_hours_ending_between(yesterday, yesterday),
             note=_day(yesterday)),
        dict(key='week_to_date', label='Week to date',
             hours=_hours_ending_between(monday, today),
             note=_day(today) if monday == today else _span(monday, today, weekday=True)),
        dict(key='previous_week', label='Previous week',
             hours=_hours_ending_between(last_monday, last_sunday),
             note=_span(last_monday, last_sunday)),
        dict(key='previous_month', label='Previous month',
             hours=_hours_ending_between(first_of_prev, last_of_prev),
             note=f'{first_of_prev:%B %Y}'),
        dict(key='active_total', label='Active projects',
             hours=active_total,
             note=f'{active_count} active project{"" if active_count == 1 else "s"}'),
    ]
