"""Admins: how they get in, how they are added, how they are removed.

Spec: docs/superpowers/specs/2026-09-15-one-business-per-install-design.md
"""
from datetime import datetime, timedelta

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import AdminInvite, Business, User, db
from gigledger import team
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


# --- invites -------------------------------------------------------------

def _invite(admin_client, app, email='assistant@example.com'):
    """Issue an invite through the UI and return the join URL it showed once."""
    admin_client.post('/settings/team/invite', data={'email': email})
    body = admin_client.get('/settings').get_data(as_text=True)
    start = body.index('/join/')
    end = body.index('"', start)
    return body[start:end]


def test_an_invite_is_shown_once_and_stored_hashed(admin_client, app):
    url = _invite(admin_client, app)
    token = url.rsplit('/', 1)[1]
    with app.app_context():
        invite = AdminInvite.query.one()
        assert invite.email == 'assistant@example.com'
        assert invite.token_hash != token
        assert invite.invited_by == 1
        assert invite.is_open()
    # A second look at the page no longer carries the link.
    assert '/join/' not in admin_client.get('/settings').get_data(as_text=True)


def test_an_invite_for_an_existing_admin_is_refused(admin_client, app):
    admin_client.post('/settings/team/invite', data={'email': 'demo@gigledger.com'})
    with app.app_context():
        assert AdminInvite.query.count() == 0


def test_reissuing_closes_the_previous_invite(admin_client, app):
    first = _invite(admin_client, app)
    _invite(admin_client, app)
    with app.app_context():
        assert AdminInvite.query.count() == 2
        assert sum(1 for i in AdminInvite.query.all() if i.is_open()) == 1
    assert app.test_client().get(first).status_code == 302  # no longer valid


def test_an_open_invite_can_be_cancelled(admin_client, app):
    _invite(admin_client, app)
    with app.app_context():
        invite_id = AdminInvite.query.one().id
    admin_client.post(f'/settings/team/invites/{invite_id}/cancel')
    with app.app_context():
        assert not AdminInvite.query.get(invite_id).is_open()


# --- join ----------------------------------------------------------------

def test_joining_creates_the_admin_and_signs_them_in(admin_client, app):
    url = _invite(admin_client, app)
    http = app.test_client()

    page = http.get(url).get_data(as_text=True)
    assert 'Demo Freelance Studio' in page

    response = http.post(url, data={'password': 'correct-horse-battery',
                                    'confirm_password': 'correct-horse-battery'})

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email='assistant@example.com').one()
        assert AdminInvite.query.one().redeemed_at is not None
    assert http.get('/').status_code == 200
    # And they see the shared books.
    assert 'Website Redesign' in http.get('/projects/').get_data(as_text=True)


def test_a_short_password_does_not_join(admin_client, app):
    url = _invite(admin_client, app)
    app.test_client().post(url, data={'password': 'short', 'confirm_password': 'short'})
    with app.app_context():
        assert User.query.filter_by(email='assistant@example.com').count() == 0


@pytest.mark.parametrize('spoil', ['expired', 'redeemed', 'unknown'])
def test_a_spoiled_invite_gets_one_and_the_same_answer(admin_client, app, spoil):
    url = _invite(admin_client, app)
    with app.app_context():
        invite = AdminInvite.query.one()
        if spoil == 'expired':
            invite.expires_at = datetime.utcnow() - timedelta(seconds=1)
        elif spoil == 'redeemed':
            invite.redeemed_at = datetime.utcnow()
        db.session.commit()
    if spoil == 'unknown':
        url = '/join/not-a-real-token'

    http = app.test_client()
    get = http.get(url)
    post = http.post(url, data={'password': 'correct-horse-battery',
                                'confirm_password': 'correct-horse-battery'})

    assert get.status_code == 302 and get.headers['Location'].endswith('/login')
    assert post.status_code == 302 and post.headers['Location'].endswith('/login')
    with app.app_context():
        assert User.query.filter_by(email='assistant@example.com').count() == 0


# --- removing admins -----------------------------------------------------

def _second_admin(app):
    with app.app_context():
        other = User(email='assistant@example.com', password_hash='x')
        db.session.add(other)
        db.session.commit()
        return other.id


def test_an_admin_can_be_removed_and_their_rows_stay(admin_client, app):
    other_id = _second_admin(app)
    from gigledger.models import Client
    with app.app_context():
        db.session.add(Client(user_id=other_id, name='Added By Assistant'))
        db.session.commit()

    admin_client.post(f'/settings/team/admins/{other_id}/remove')

    with app.app_context():
        assert User.query.get(other_id) is None
        assert Client.query.filter_by(name='Added By Assistant').count() == 1


def test_you_cannot_remove_yourself(admin_client, app):
    _second_admin(app)
    admin_client.post('/settings/team/admins/1/remove')
    with app.app_context():
        assert User.query.get(1) is not None


def test_the_last_admin_cannot_be_removed(admin_client, app):
    other_id = _second_admin(app)
    login_as(app, other_id).post('/settings/team/admins/1/remove')
    with app.app_context():
        assert User.query.count() == 1
    # Now the survivor is the last admin: nobody can remove them.
    login_as(app, other_id).post(f'/settings/team/admins/{other_id}/remove')
    with app.app_context():
        assert User.query.count() == 1


def test_the_team_panel_lists_admins_and_open_invites(admin_client, app):
    _second_admin(app)
    _invite(admin_client, app, email='new@example.com')
    body = admin_client.get('/settings').get_data(as_text=True)
    assert 'demo@gigledger.com' in body
    assert 'assistant@example.com' in body
    assert 'new@example.com' in body
