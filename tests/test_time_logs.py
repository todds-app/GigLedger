"""Time logs: a server-side timer per project, hours as rows, bill a log.

Recorded in docs/adr/0015. Hours used to be one mutable float on the project;
now every logged block is a TimeLog with a date range, an optional 1:1 link to
the income Transaction it produced, and the timer's unlogged seconds live on
the Project row so they survive a reload and a restart.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import (INCOME, Project, TimeLog, Transaction, db,
                              quarter_hours, round_quarter)
from conftest import login_as


def a_project(app, **overrides):
    """The id of a fresh hourly project (rate 125) with no logs and no timer."""
    with app.app_context():
        fields = dict(user_id=1, name='Timed', rate_type='hourly', rate=125,
                      status='active')
        fields.update(overrides)
        project = Project(**fields)
        db.session.add(project)
        db.session.commit()
        return project.id


def a_log(app, project_id, hours, ended_days_ago=0, **overrides):
    with app.app_context():
        ended = datetime.now() - timedelta(days=ended_days_ago)
        fields = dict(user_id=1, project_id=project_id, hours=hours,
                      started_on=ended - timedelta(days=1), ended_on=ended)
        fields.update(overrides)
        log = TimeLog(**fields)
        db.session.add(log)
        db.session.commit()
        return log.id


# --- Model -----------------------------------------------------------------

def test_a_project_sums_its_time_logs(app):
    pid = a_project(app)
    a_log(app, pid, 1.5)
    a_log(app, pid, 2.25)
    with app.app_context():
        project = db.session.get(Project, pid)
        assert project.hours_logged == 3.75
        assert project.earned == 3.75 * 125


def test_a_project_without_logs_has_zero_hours(app):
    pid = a_project(app)
    with app.app_context():
        project = db.session.get(Project, pid)
        assert project.hours_logged == 0
        assert project.earned == 0
        assert project.progress == 0


def test_fixed_progress_keeps_its_formula(app):
    pid = a_project(app, rate_type='fixed', rate=5000)
    a_log(app, pid, 40)
    with app.app_context():
        assert db.session.get(Project, pid).progress == 40


@pytest.mark.parametrize('hours,expected', [
    (0, 0), (0.374, 0.25), (0.375, 0.5), (1.1, 1.0), (1.13, 1.25), (2.0, 2.0),
])
def test_round_quarter_rounds_half_up(hours, expected):
    assert round_quarter(hours) == expected


@pytest.mark.parametrize('seconds,expected', [
    (0, 0), (-5, 0), (1, 0.25), (899, 0.25), (1350, 0.5), (3960, 1.0), (4068, 1.25),
])
def test_quarter_hours_never_rounds_a_started_timer_to_nothing(seconds, expected):
    assert quarter_hours(seconds) == expected


def test_start_stop_accumulates_and_keeps_since(app):
    pid = a_project(app)
    t = datetime(2026, 9, 16, 9, 0, 0)
    with app.app_context():
        project = db.session.get(Project, pid)
        assert project.timer_running is False
        assert project.elapsed_seconds(t) == 0

        project.start_timer(t)
        assert project.timer_running is True
        assert project.timer_since == t
        assert project.elapsed_seconds(t + timedelta(seconds=30)) == 30

        project.stop_timer(t + timedelta(seconds=90))
        assert project.timer_running is False
        assert project.timer_seconds == 90
        assert project.timer_since == t

        project.start_timer(t + timedelta(seconds=200))
        assert project.timer_since == t
        assert project.elapsed_seconds(t + timedelta(seconds=210)) == 100
        assert project.timer_label(t + timedelta(seconds=210)) == '0:01:40'
        assert project.suggested_hours(t + timedelta(seconds=210)) == 0.25

        project.reset_timer()
        assert project.timer_running is False
        assert project.timer_seconds == 0
        assert project.timer_since is None


def test_deleting_a_project_deletes_its_logs_but_not_their_transactions(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    with app.app_context():
        tx = Transaction(user_id=1, amount=250, date=datetime.now(), kind=INCOME)
        db.session.add(tx)
        db.session.flush()
        db.session.get(TimeLog, lid).transaction_id = tx.id
        db.session.commit()
        tx_id = tx.id

        db.session.delete(db.session.get(Project, pid))
        db.session.commit()
        assert db.session.get(TimeLog, lid) is None
        assert db.session.get(Transaction, tx_id) is not None


def test_deleting_a_transaction_nulls_the_log_link(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    with app.app_context():
        tx = Transaction(user_id=1, amount=250, date=datetime.now(), kind=INCOME)
        db.session.add(tx)
        db.session.flush()
        log = db.session.get(TimeLog, lid)
        log.transaction = tx
        db.session.commit()
        assert log.is_billed is True

        db.session.delete(tx)
        db.session.commit()
        log = db.session.get(TimeLog, lid)
        assert log is not None
        assert log.transaction_id is None
        assert log.is_billed is False


# --- Migration and seed ----------------------------------------------------

def test_an_existing_projects_table_gains_timer_columns_and_its_hours_become_one_log(
        tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120), "
                 "password_hash VARCHAR(200))")
    conn.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER, "
                 "name VARCHAR(200), hours_logged FLOAT, start_date DATETIME)")
    conn.execute("INSERT INTO users (id, email, password_hash) VALUES (7, 'a@b.c', 'x')")
    conn.execute("INSERT INTO projects VALUES (3, 7, 'Old', 12.5, '2026-08-01 09:00:00')")
    conn.execute("INSERT INTO projects VALUES (4, 7, 'Idle', 0, '2026-08-01 09:00:00')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(gigledger.app, 'DB_PATH', path)

    create_app()
    create_app()  # idempotent: the second start must not add a second log

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    assert {'timer_started_at', 'timer_seconds', 'timer_since'} <= columns
    logs = conn.execute("SELECT project_id, user_id, hours, started_on FROM time_logs").fetchall()
    assert logs == [(3, 7, 12.5, '2026-08-01 09:00:00')]
    assert conn.execute("SELECT hours_logged FROM projects WHERE id = 3").fetchone()[0] == 0
    conn.close()


def test_the_demo_seed_creates_time_logs(app):
    with app.app_context():
        assert TimeLog.query.count() >= 7
        for project in Project.query.all():
            assert project.hours_logged > 0
        first_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0)
        assert TimeLog.query.filter(TimeLog.ended_on >= first_of_month).count() > 0


# --- Timer routes ----------------------------------------------------------

def start(client, pid):
    return client.post(f'/projects/{pid}/timer/start')


def stop(client, pid):
    return client.post(f'/projects/{pid}/timer/stop')


def project(app, pid):
    with app.app_context():
        return db.session.get(Project, pid)


def set_timer(app, pid, running_for=None, seconds=0, since=None):
    with app.app_context():
        p = db.session.get(Project, pid)
        p.timer_seconds = seconds
        p.timer_since = since
        p.timer_started_at = (datetime.now() - running_for) if running_for else None
        db.session.commit()


def test_start_sets_the_timer_running(app):
    pid = a_project(app)
    response = start(login_as(app), pid)
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/projects/')
    p = project(app, pid)
    assert p.timer_running is True
    assert p.timer_since is not None


def test_stop_accumulates_seconds_and_keeps_since(app):
    pid = a_project(app)
    since = datetime(2026, 9, 1, 9, 0)
    set_timer(app, pid, running_for=timedelta(seconds=90), seconds=10, since=since)
    stop(login_as(app), pid)
    p = project(app, pid)
    assert p.timer_running is False
    assert 100 <= p.timer_seconds <= 102
    assert p.timer_since == since


def test_starting_a_second_project_stops_the_first(app):
    a, b = a_project(app, name='A'), a_project(app, name='B')
    set_timer(app, a, running_for=timedelta(seconds=60))
    start(login_as(app), b)
    pa, pb = project(app, a), project(app, b)
    assert pa.timer_running is False
    assert pa.timer_seconds >= 60
    assert pb.timer_running is True
    with app.app_context():
        assert Project.query.filter(Project.timer_started_at.isnot(None)).count() == 1


def test_start_twice_is_a_noop(app):
    pid = a_project(app)
    client = login_as(app)
    start(client, pid)
    started_at = project(app, pid).timer_started_at
    start(client, pid)
    assert project(app, pid).timer_started_at == started_at


def test_stop_when_idle_changes_nothing(app):
    pid = a_project(app)
    set_timer(app, pid, seconds=42)
    stop(login_as(app), pid)
    p = project(app, pid)
    assert p.timer_seconds == 42
    assert p.timer_running is False


def test_leaving_active_stops_the_timer(app):
    pid = a_project(app)
    set_timer(app, pid, running_for=timedelta(seconds=30))
    login_as(app).post(f'/projects/update-status/{pid}', data={'status': 'on_hold'})
    p = project(app, pid)
    assert p.timer_running is False
    assert p.timer_seconds >= 30


def test_the_card_offers_start_when_idle_and_stop_when_running(app):
    pid = a_project(app)
    client = login_as(app)

    body = client.get('/projects/').get_data(as_text=True)
    assert f'/projects/{pid}/timer/start' in body
    assert f'/projects/{pid}/timer/stop' not in body
    assert 'data-timer-running="1"' not in body

    set_timer(app, pid, running_for=timedelta(seconds=125), since=datetime(2026, 9, 2, 8, 0))
    body = client.get('/projects/').get_data(as_text=True)
    assert f'/projects/{pid}/timer/stop' in body
    assert f'/projects/{pid}/timer/start' not in body
    assert 'data-timer-running="1"' in body
    assert 'data-elapsed="125"' in body or 'data-elapsed="126"' in body
    assert f'openLogHoursModal({pid}, ' in body
    assert '&#34;2026-09-02&#34;' in body  # tojson|forceescape inside on*=


def test_the_list_page_never_writes(app):
    pid = a_project(app)
    set_timer(app, pid, running_for=timedelta(seconds=10))
    login_as(app).get('/projects/')
    assert project(app, pid).timer_running is True


# --- Log Hours -------------------------------------------------------------

def log_hours(client, pid, **form):
    return client.post(f'/projects/log-hours/{pid}', data=form, follow_redirects=True)


def logs_of(app, pid):
    with app.app_context():
        return TimeLog.query.filter_by(project_id=pid).order_by(TimeLog.id).all()


def test_logging_hours_creates_a_log_and_resets_the_timer(app):
    pid = a_project(app)
    set_timer(app, pid, seconds=5400, since=datetime(2026, 9, 13, 10, 0))
    log_hours(login_as(app), pid, hours='1.5', started_on='2026-09-13', ended_on='2026-09-16')
    (log,) = logs_of(app, pid)
    assert log.hours == 1.5
    assert log.user_id == 1
    assert log.started_on.date() == datetime(2026, 9, 13).date()
    assert log.ended_on.date() == datetime(2026, 9, 16).date()
    with app.app_context():
        p = db.session.get(Project, pid)
        assert (p.timer_running, p.timer_seconds, p.timer_since) == (False, 0, None)
        assert p.hours_logged == 1.5


def test_logging_on_a_running_timer_stops_it_first(app):
    pid = a_project(app)
    set_timer(app, pid, running_for=timedelta(seconds=30))
    log_hours(login_as(app), pid, hours='0.25')
    assert project(app, pid).timer_running is False
    assert len(logs_of(app, pid)) == 1


def test_logging_with_the_timer_at_zero_still_works(app):
    pid = a_project(app)
    log_hours(login_as(app), pid, hours='2')
    assert [l.hours for l in logs_of(app, pid)] == [2.0]


def test_posted_hours_are_rounded_to_the_quarter(app):
    pid = a_project(app)
    log_hours(login_as(app), pid, hours='1.1')
    assert [l.hours for l in logs_of(app, pid)] == [1.0]


@pytest.mark.parametrize('bad', ['0', '-1', 'x', '', '0.1'])
def test_bad_hours_are_refused(app, bad):
    pid = a_project(app)
    body = log_hours(login_as(app), pid, hours=bad).get_data(as_text=True)
    assert logs_of(app, pid) == []
    assert 'hours' in body.lower()


def test_dates_default_to_timer_since_and_today(app):
    pid = a_project(app)
    set_timer(app, pid, seconds=900, since=datetime(2026, 9, 10, 8, 0))
    log_hours(login_as(app), pid, hours='0.25')
    (log,) = logs_of(app, pid)
    assert log.started_on.date() == datetime(2026, 9, 10).date()
    assert log.ended_on.date() == datetime.now().date()


def test_a_start_after_the_end_is_refused(app):
    pid = a_project(app)
    log_hours(login_as(app), pid, hours='1', started_on='2026-09-16', ended_on='2026-09-15')
    assert logs_of(app, pid) == []


def test_logging_hours_never_creates_a_transaction(app):
    """The old route booked the project's cumulative earnings as income."""
    pid = a_project(app)
    with app.app_context():
        before = Transaction.query.count()
    log_hours(login_as(app), pid, hours='3', create_transaction='on')
    with app.app_context():
        assert Transaction.query.count() == before


def test_hours_this_month_sums_logs_that_ended_this_month(app):
    with app.app_context():
        TimeLog.query.delete()
        db.session.commit()
    pid = a_project(app, start_date=datetime(2020, 1, 1))
    a_log(app, pid, 2, ended_days_ago=0)
    a_log(app, pid, 5, ended_days_ago=60)
    body = login_as(app).get('/projects/').get_data(as_text=True)
    hours_card = body[body.index('Logged this month') - 200:body.index('Logged this month')]
    assert '2.0h' in hours_card


def test_the_modal_has_date_fields_and_no_transaction_checkbox(app):
    body = login_as(app).get('/projects/').get_data(as_text=True)
    assert 'name="started_on"' in body and 'name="ended_on"' in body
    assert 'create_transaction' not in body


# --- Hours Logged on the project page --------------------------------------

def bill(app, lid, amount=1):
    """Link a transaction by hand; the route that does it is tested below."""
    with app.app_context():
        tx = Transaction(user_id=1, amount=amount, date=datetime.now(), kind=INCOME)
        db.session.add(tx)
        db.session.flush()
        db.session.get(TimeLog, lid).transaction_id = tx.id
        db.session.commit()
        return tx.id


def a_log_row(app, lid):
    with app.app_context():
        return db.session.get(TimeLog, lid)


def test_the_detail_page_lists_logs_newest_first(app):
    pid = a_project(app)
    older = a_log(app, pid, 2, ended_days_ago=40)
    newer = a_log(app, pid, 3, ended_days_ago=1)
    body = login_as(app).get(f'/projects/{pid}').get_data(as_text=True)
    assert 'id="hours-logged"' in body
    assert body.index('id="hours-logged"') < body.index('id="documents"')
    assert body.index(f'time-logs/{newer}/') < body.index(f'time-logs/{older}/')


def test_editing_hours_on_an_unbilled_log(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    response = login_as(app).post(f'/projects/time-logs/{lid}/edit', data={'hours': '3.1'})
    assert response.headers['Location'].endswith(f'/projects/{pid}')
    assert a_log_row(app, lid).hours == 3.0


@pytest.mark.parametrize('bad', ['0', '-2', 'x', ''])
def test_editing_to_bad_hours_is_refused(app, bad):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    login_as(app).post(f'/projects/time-logs/{lid}/edit', data={'hours': bad})
    assert a_log_row(app, lid).hours == 2


def test_deleting_an_unbilled_log(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    login_as(app).post(f'/projects/time-logs/{lid}/delete')
    assert a_log_row(app, lid) is None


def test_edit_and_delete_are_refused_once_billed(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    bill(app, lid)
    client = login_as(app)
    body = client.post(f'/projects/time-logs/{lid}/edit', data={'hours': '9'},
                       follow_redirects=True).get_data(as_text=True)
    assert 'transaction' in body.lower()
    client.post(f'/projects/time-logs/{lid}/delete')
    log = a_log_row(app, lid)
    assert log is not None
    assert log.hours == 2


def test_an_unbilled_log_shows_its_controls(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    body = login_as(app).get(f'/projects/{pid}').get_data(as_text=True)
    for action in ('edit', 'delete', 'create-transaction'):
        assert f'/projects/time-logs/{lid}/{action}' in body
    assert 'Transaction created' not in body


def test_a_billed_log_shows_the_link_and_hides_the_controls(app):
    pid = a_project(app)
    lid = a_log(app, pid, 2)
    bill(app, lid)
    body = login_as(app).get(f'/projects/{pid}').get_data(as_text=True)
    assert 'Transaction created' in body
    for action in ('edit', 'delete', 'create-transaction'):
        assert f'/projects/time-logs/{lid}/{action}' not in body
    assert '/transactions' in body


def test_the_missing_log_is_a_404(app):
    assert login_as(app).post('/projects/time-logs/9999/edit', data={'hours': '1'}).status_code == 404


# --- Create Transaction ----------------------------------------------------

def create_tx(client, lid):
    return client.post(f'/projects/time-logs/{lid}/create-transaction', follow_redirects=True)


def test_creating_a_transaction_from_a_log(app):
    pid = a_project(app, name='Retainer', rate=125)
    lid = a_log(app, pid, 1.5, ended_days_ago=2)
    create_tx(login_as(app), lid)
    with app.app_context():
        log = db.session.get(TimeLog, lid)
        assert log.is_billed
        tx = log.transaction
        assert tx.amount == 187.5
        assert tx.kind == INCOME
        assert tx.category == 'Freelance Project'
        assert tx.source == 'project'
        assert tx.is_tax_deductible is False
        assert tx.user_id == 1
        assert tx.date == log.ended_on
        assert '1.5h' in tx.description and 'Retainer' in tx.description
        assert tx.time_log is log


def test_a_second_create_is_refused(app):
    pid = a_project(app)
    lid = a_log(app, pid, 1)
    client = login_as(app)
    create_tx(client, lid)
    with app.app_context():
        before = Transaction.query.count()
        tx_id = db.session.get(TimeLog, lid).transaction_id
    create_tx(client, lid)
    with app.app_context():
        assert Transaction.query.count() == before
        assert db.session.get(TimeLog, lid).transaction_id == tx_id


@pytest.mark.parametrize('rate_type', ['fixed', 'daily'])
def test_create_is_refused_for_non_hourly_projects(app, rate_type):
    pid = a_project(app, rate_type=rate_type, rate=5000)
    lid = a_log(app, pid, 4)
    client = login_as(app)
    assert f'time-logs/{lid}/create-transaction' not in client.get(f'/projects/{pid}').get_data(as_text=True)
    create_tx(client, lid)
    assert a_log_row(app, lid).is_billed is False


def test_create_is_refused_when_the_rate_is_zero(app):
    pid = a_project(app, rate=0)
    lid = a_log(app, pid, 4)
    body = create_tx(login_as(app), lid).get_data(as_text=True)
    assert a_log_row(app, lid).is_billed is False
    assert 'rate' in body.lower()


def test_deleting_the_transaction_through_the_route_unlocks_the_log(app):
    pid = a_project(app)
    lid = a_log(app, pid, 1)
    client = login_as(app)
    create_tx(client, lid)
    tx_id = a_log_row(app, lid).transaction_id
    client.post(f'/transactions/delete/{tx_id}')
    log = a_log_row(app, lid)
    assert log is not None
    assert log.is_billed is False
    client.post(f'/projects/time-logs/{lid}/edit', data={'hours': '2'})
    assert a_log_row(app, lid).hours == 2


def test_deleting_a_project_leaves_the_transaction(app):
    pid = a_project(app)
    lid = a_log(app, pid, 1)
    client = login_as(app)
    create_tx(client, lid)
    tx_id = a_log_row(app, lid).transaction_id
    client.post(f'/projects/delete/{pid}')
    with app.app_context():
        tx = db.session.get(Transaction, tx_id)
        assert tx is not None
        assert tx.time_log is None
