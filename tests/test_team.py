"""Admins: how they get in, how they are added, how they are removed.

Spec: docs/superpowers/specs/2026-09-15-one-business-per-install-design.md
"""
import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import Business, User, db
from tests.conftest import build_app, login_as


@pytest.fixture
def empty_app(tmp_path, monkeypatch):
    """A fresh install: no SEED_DEMO, so no users and no business."""
    monkeypatch.delenv('SEED_DEMO', raising=False)
    return build_app(tmp_path, monkeypatch)


# --- sign-up is gone, the seed is opt-in ---------------------------------

def test_signup_no_longer_exists(app):
    assert app.test_client().get('/signup').status_code == 404
    assert 'auth.signup' not in {r.endpoint for r in app.url_map.iter_rules()}


def test_the_login_page_does_not_link_to_signup(app):
    body = app.test_client().get('/login').get_data(as_text=True)
    assert 'Sign up' not in body
    assert '/signup' not in body


def test_the_demo_seed_does_not_run_without_the_flag(empty_app):
    with empty_app.app_context():
        assert User.query.count() == 0
        assert Business.query.count() == 0


def test_the_demo_seed_runs_with_the_flag(app):
    with app.app_context():
        assert User.query.filter_by(email='demo@gigledger.com').count() == 1


# --- first run -----------------------------------------------------------

def test_login_redirects_to_setup_while_there_are_no_admins(empty_app):
    response = empty_app.test_client().get('/login')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/setup')


def test_setup_creates_the_business_and_the_first_admin(empty_app):
    http = empty_app.test_client()
    response = http.post('/setup', data={
        'business_name': 'Dani Smith Design',
        'email': 'dani@example.com',
        'password': 'correct-horse-battery',
        'confirm_password': 'correct-horse-battery'})

    assert response.status_code == 302
    with empty_app.app_context():
        assert Business.get().name == 'Dani Smith Design'
        user = User.query.one()
        assert user.email == 'dani@example.com'
    # Signed in: the dashboard renders rather than bouncing to /login.
    assert http.get('/').status_code == 200


def test_setup_rejects_a_short_password(empty_app):
    empty_app.test_client().post('/setup', data={
        'business_name': 'X', 'email': 'dani@example.com',
        'password': 'short', 'confirm_password': 'short'})
    with empty_app.app_context():
        assert User.query.count() == 0


def test_setup_is_a_404_once_an_admin_exists(app):
    assert app.test_client().get('/setup').status_code == 404
    response = app.test_client().post('/setup', data={
        'business_name': 'X', 'email': 'intruder@example.com',
        'password': 'correct-horse-battery', 'confirm_password': 'correct-horse-battery'})
    assert response.status_code == 404
    with app.app_context():
        assert User.query.filter_by(email='intruder@example.com').count() == 0


def test_login_records_last_login_at(app):
    from gigledger.app import bcrypt
    with app.app_context():
        user = User.query.get(1)
        user.password_hash = bcrypt.generate_password_hash('demo1234').decode('utf-8')
        db.session.commit()
    app.test_client().post('/login', data={'email': 'demo@gigledger.com',
                                           'password': 'demo1234'})
    with app.app_context():
        assert User.query.get(1).last_login_at is not None


def test_login_accepts_the_email_in_any_case(app):
    from gigledger.app import bcrypt
    with app.app_context():
        user = User.query.get(1)
        user.password_hash = bcrypt.generate_password_hash('demo1234').decode('utf-8')
        db.session.commit()
    response = app.test_client().post('/login', data={'email': 'Demo@GigLedger.com',
                                                      'password': 'demo1234'})
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/')
