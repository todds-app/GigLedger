"""Hours Worked on the dashboard.

Five figures from the time logs: the previous day, week to date, the previous
week, the previous month, and every hour on projects that are active now.
A log covers a date range; like "Logged this month" on the Projects page and
like billing (ADR-0015), it counts on the day it ended. Weeks start Monday.
Unlogged timer time is not hours worked until it is logged.
"""
from datetime import date, datetime

import pytest

from design_portal.hours import hours_worked
from design_portal.models import Project, TimeLog, db
from conftest import login_as

# Wednesday. Week to date is Mon 9 - Wed 11 Nov; the previous week is
# Mon 2 - Sun 8 Nov; the previous day is Tue 10 Nov; the previous month is October.
TODAY = date(2026, 11, 11)


@pytest.fixture
def clean(app):
    """The demo seed's logs are cleared so every figure is known."""
    with app.app_context():
        TimeLog.query.delete()
        db.session.commit()
    return app


def a_project(app, status='active', name='P'):
    with app.app_context():
        p = Project(user_id=1, name=name, rate_type='hourly', rate=100, status=status)
        db.session.add(p)
        db.session.commit()
        return p.id


def log(app, pid, hours, ended, started=None):
    with app.app_context():
        db.session.add(TimeLog(user_id=1, project_id=pid, hours=hours,
                               started_on=datetime.combine(started or ended, datetime.min.time()),
                               ended_on=datetime.combine(ended, datetime.min.time())))
        db.session.commit()


def figures(app, today=TODAY):
    with app.app_context():
        return {f['key']: f['hours'] for f in hours_worked(today)}


def test_the_five_figures_in_order(clean):
    with clean.app_context():
        keys = [f['key'] for f in hours_worked(TODAY)]
    assert keys == ['previous_day', 'week_to_date', 'previous_week',
                    'previous_month', 'active_total']


def test_nothing_logged_is_all_zero(clean):
    assert set(figures(clean).values()) == {0}


def test_each_log_lands_in_the_windows_its_end_day_falls_in(clean):
    pid = a_project(clean)
    log(clean, pid, 1.0, date(2026, 11, 10))    # yesterday, and this week
    log(clean, pid, 2.0, date(2026, 11, 11))    # today: this week only
    log(clean, pid, 4.0, date(2026, 11, 8))     # Sunday: previous week
    log(clean, pid, 8.0, date(2026, 11, 2))     # Monday: previous week
    log(clean, pid, 16.0, date(2026, 10, 31))   # October
    log(clean, pid, 32.0, date(2026, 10, 1))    # October
    log(clean, pid, 64.0, date(2026, 9, 30))    # September: active total only
    assert figures(clean) == {
        'previous_day': 1.0,
        'week_to_date': 3.0,
        'previous_week': 12.0,
        'previous_month': 48.0,
        'active_total': 127.0,
    }


def test_a_multi_day_log_counts_on_the_day_it_ended(clean):
    pid = a_project(clean)
    log(clean, pid, 5.0, ended=date(2026, 11, 9), started=date(2026, 11, 5))
    f = figures(clean)
    assert f['week_to_date'] == 5.0
    assert f['previous_week'] == 0


def test_a_log_ended_later_in_the_day_still_counts_on_that_day(clean):
    """Migrated rows carry a time of day on ended_on."""
    pid = a_project(clean)
    with clean.app_context():
        db.session.add(TimeLog(user_id=1, project_id=pid, hours=1.5,
                               started_on=datetime(2026, 11, 10, 9, 0),
                               ended_on=datetime(2026, 11, 10, 23, 59, 59)))
        db.session.commit()
    assert figures(clean)['previous_day'] == 1.5


def test_the_windows_count_every_project_but_the_total_only_active_ones(clean):
    active, done = a_project(clean), a_project(clean, status='completed')
    log(clean, active, 1.0, date(2026, 11, 10))
    log(clean, done, 2.0, date(2026, 11, 10))
    f = figures(clean)
    assert f['previous_day'] == 3.0
    assert f['active_total'] == 1.0


def test_on_a_monday_week_to_date_is_today_and_the_previous_day_is_sunday(clean):
    pid = a_project(clean)
    log(clean, pid, 2.0, date(2026, 11, 8))    # Sunday
    log(clean, pid, 1.0, date(2026, 11, 9))    # Monday
    f = figures(clean, today=date(2026, 11, 9))
    assert f['previous_day'] == 2.0
    assert f['week_to_date'] == 1.0
    assert f['previous_week'] == 2.0


def test_in_january_the_previous_month_is_last_december(clean):
    pid = a_project(clean)
    log(clean, pid, 3.0, date(2025, 12, 31))
    assert figures(clean, today=date(2026, 1, 5))['previous_month'] == 3.0


def test_each_figure_names_its_dates(clean):
    with clean.app_context():
        notes = {f['key']: f['note'] for f in hours_worked(TODAY)}
    assert notes['previous_day'] == 'Tue 10 Nov'
    assert notes['week_to_date'] == 'Mon 9 – Wed 11 Nov'
    assert notes['previous_week'] == '2 – 8 Nov'
    assert notes['previous_month'] == 'October 2026'


def test_the_week_note_spans_two_months(clean):
    with clean.app_context():
        notes = {f['key']: f['note'] for f in hours_worked(date(2026, 11, 4))}
    assert notes['previous_week'] == '26 Oct – 1 Nov'


def test_the_active_total_names_how_many_projects(clean):
    a_project(clean, name='A'), a_project(clean, name='B'), a_project(clean, status='on_hold')
    with clean.app_context():
        note = {f['key']: f['note'] for f in hours_worked(TODAY)}['active_total']
        active = Project.query.filter_by(status='active').count()
    assert note == f'{active} active project{"" if active == 1 else "s"}'


def test_the_dashboard_shows_the_section(clean):
    pid = a_project(clean)
    log(clean, pid, 2.5, date.today())
    body = login_as(clean).get('/').get_data(as_text=True)
    assert 'Hours Worked' in body
    for label in ('Previous day', 'Week to date', 'Previous week',
                  'Previous month', 'Active projects'):
        assert label in body
    assert '2.5h' in body
