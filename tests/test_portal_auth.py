"""Client portal authentication.

The decision under test is docs/adr/0008. The hazard that shaped it is worth
restating, because every test here is downstream of it:

A `Client` is not a `User`. If a client logged in through Flask-Login, then
`current_user` would sometimes be a portal account - and all ~40 existing routes
do `Model.query.filter_by(user_id=current_user.id)`. A portal account with id 3
hitting /transactions would be served **freelancer #3's** transactions. Not a
missing check: a type confusion that turns every existing ownership filter into
a cross-tenant leak.

So the portal deliberately does not use Flask-Login. `@login_required` and
`current_user` keep meaning *freelancer*, exactly as all existing code already
assumes, and a portal session cannot satisfy them. `test_a_portal_session_cannot_reach_the_freelancer_app`
is the test that pins that down, and it is the most important one in the file.

The route-walking test mirrors tests/test_csrf.py: routes are discovered, not
listed, so a portal route added later is covered without anyone remembering. It
carries a vacuity check for the same reason that one does.
"""
from datetime import datetime, timedelta

import re
import pytest

import gigledger.app
import gigledger.documents
import gigledger.portal_auth as portal_auth
from gigledger.app import create_app
from gigledger.models import Client, PortalAccount, PortalInvite, User, db


# Portal routes reachable without a portal session, by design: you cannot log in
# from inside a session you do not have. An entry here is a decision that needs
# justifying in review, not a convenience.
EXPECTED_ANONYMOUS = {'portal.login', 'portal.redeem'}


def build_app(tmp_path, monkeypatch, **config):
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(gigledger.documents, 'UPLOAD_ROOT', str(tmp_path / 'uploads'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key',
                      WTF_CSRF_ENABLED=False, **config)
    app.login_manager.session_protection = None
    return app


@pytest.fixture
def app(tmp_path, monkeypatch):
    return build_app(tmp_path, monkeypatch)


def a_client_id(app, user_id=1):
    with app.app_context():
        return Client.query.filter_by(user_id=user_id).first().id


def invite_for(app, client_id, email='billing@acmecorp.com'):
    """Create an invite and return its one-time token."""
    with app.app_context():
        client = db.session.get(Client, client_id)
        _, token = portal_auth.create_invite(client, email)
        db.session.commit()
        return token


def redeem(app, token, password='portal-pass-1234', **extra):
    http = app.test_client()
    http.post(f'/portal/invite/{token}',
              data={'password': password, 'confirm_password': password, **extra},
              follow_redirects=True)
    return http


def portal_session(app, account_id, epoch=None):
    if epoch is None:
        with app.app_context():
            epoch = db.session.get(PortalAccount, account_id).session_epoch
    http = app.test_client()
    with http.session_transaction() as session:
        session['portal_account_id'] = account_id
        session['portal_epoch'] = epoch
    return http


def an_account(app, client_id=None, email='billing@acmecorp.com'):
    """A redeemed portal account, via the real invite flow."""
    client_id = client_id or a_client_id(app)
    token = invite_for(app, client_id, email)
    redeem(app, token)
    with app.app_context():
        return PortalAccount.query.filter_by(email=email).one().id


# --- the boundary --------------------------------------------------------

def test_a_portal_session_cannot_reach_the_freelancer_app(app):
    """The whole reason the portal does not use Flask-Login. An admin sees
    every row (ADR-0014), so a portal account must never be `current_user`:
    it holds an id from a different table, and satisfying @login_required
    with it would hand a client every business's books."""
    account_id = an_account(app)
    http = portal_session(app, account_id)

    # Trailing slashes matter: Flask answers /projects with a 308 to /projects/
    # before authentication is consulted, which would prove nothing.
    for path in ('/', '/transactions', '/projects/', '/invoices', '/settings'):
        response = http.get(path)
        assert response.status_code == 302, path
        assert '/login' in response.headers['Location'], path
        assert '/portal' not in response.headers['Location'], path


def test_a_freelancer_session_cannot_reach_the_portal(app):
    """The mirror image, and not symmetric by accident: a portal session and
    an admin session are mutually exclusive, so the freelancer session key
    must never double as portal access either."""
    an_account(app)
    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True

    response = http.get('/portal/')

    assert response.status_code == 302
    assert '/portal/login' in response.headers['Location']


def test_logging_into_the_portal_drops_the_freelancer_session(app):
    """Two principals in one session is a state where a decorator's order
    decides who you are. There is no such state."""
    an_account(app)
    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True

    http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                     'password': 'portal-pass-1234'})

    with http.session_transaction() as session:
        assert 'portal_account_id' in session
        assert '_user_id' not in session


def test_logging_into_the_freelancer_app_drops_the_portal_session(app):
    account_id = an_account(app)
    http = portal_session(app, account_id)

    http.post('/login', data={'email': 'demo@gigledger.com', 'password': 'demo1234'})

    with http.session_transaction() as session:
        assert 'portal_account_id' not in session


# --- every portal route is guarded ---------------------------------------

def portal_rules(app):
    return [r for r in app.url_map.iter_rules()
            if r.endpoint.startswith('portal.') and r.endpoint not in EXPECTED_ANONYMOUS]


def routes_that_answered(app, make_client):
    """Portal routes that responded to a request as though the caller were
    entitled to it - anything that is not a bounce to the portal login.

    A fresh client per rule, because some of these routes change the session:
    probing /portal/logout with a shared client would sign the probe out and
    make every route examined afterwards look guarded.
    """
    answered = []
    for rule in portal_rules(app):
        method = 'POST' if 'POST' in rule.methods else 'GET'
        path = re.sub(r'<int:\w+>', '1', rule.rule)
        response = make_client().open(path, method=method)
        bounced = (response.status_code == 302
                   and '/portal/login' in response.headers.get('Location', ''))
        if not bounced:
            answered.append((rule.endpoint, response.status_code))
    return answered


def test_every_portal_route_refuses_an_anonymous_caller(app):
    assert portal_rules(app), 'no portal routes discovered - the probe is empty'
    assert routes_that_answered(app, app.test_client) == []


def test_the_probe_is_not_vacuous(app):
    """Proves the previous test can fail. If an authenticated portal session is
    also bounced, the probe is not reaching handlers and proves nothing."""
    account_id = an_account(app)

    assert routes_that_answered(app, lambda: portal_session(app, account_id)) != []


# --- invites -------------------------------------------------------------

def test_the_invite_token_is_not_stored_in_the_database(app):
    """A database read - a backup, a stray SELECT - must not hand over working
    invites."""
    token = invite_for(app, a_client_id(app))

    with app.app_context():
        stored = PortalInvite.query.one()
        assert token not in (stored.token_hash or '')
        assert len(token) > 20


def test_an_invite_can_only_be_redeemed_once(app):
    token = invite_for(app, a_client_id(app))
    redeem(app, token)

    redeem(app, token, password='second-attempt-1234')

    with app.app_context():
        assert PortalAccount.query.count() == 1
        account = PortalAccount.query.one()
        assert portal_auth.check_password(account, 'portal-pass-1234')
        assert not portal_auth.check_password(account, 'second-attempt-1234')


def test_an_expired_invite_is_refused(app):
    client_id = a_client_id(app)
    token = invite_for(app, client_id)
    with app.app_context():
        invite = PortalInvite.query.one()
        invite.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

    redeem(app, token)

    with app.app_context():
        assert PortalAccount.query.count() == 0


def test_reissuing_an_invite_invalidates_the_previous_one(app):
    client_id = a_client_id(app)
    first = invite_for(app, client_id)
    second = invite_for(app, client_id)

    redeem(app, first)

    with app.app_context():
        assert PortalAccount.query.count() == 0
    redeem(app, second)
    with app.app_context():
        assert PortalAccount.query.count() == 1


def test_redeeming_onto_an_existing_account_requires_its_password(app):
    """Two freelancers may both have a client at the same address. Linking a new
    client to an existing portal account must prove control of that account -
    otherwise holding an invite is enough to reach another freelancer's
    documents through it."""
    first_client = a_client_id(app)
    an_account(app, first_client, email='shared@example.com')

    with app.app_context():
        other = Client(user_id=1, name='Second Freelancer Client')
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    token = invite_for(app, other_id, email='shared@example.com')

    redeem(app, token, password='attacker-chosen-1234')

    with app.app_context():
        account = PortalAccount.query.filter_by(email='shared@example.com').one()
        assert portal_auth.check_password(account, 'portal-pass-1234')
        assert not portal_auth.check_password(account, 'attacker-chosen-1234')
        assert db.session.get(Client, other_id).portal_account_id is None


def test_redeeming_onto_an_existing_account_links_when_the_password_is_right(app):
    an_account(app, a_client_id(app), email='shared@example.com')
    with app.app_context():
        other = Client(user_id=1, name='Second Freelancer Client')
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    token = invite_for(app, other_id, email='shared@example.com')

    redeem(app, token, password='portal-pass-1234')

    with app.app_context():
        account = PortalAccount.query.filter_by(email='shared@example.com').one()
        assert db.session.get(Client, other_id).portal_account_id == account.id
        assert PortalAccount.query.count() == 1


# --- revocation ----------------------------------------------------------

def test_revoking_access_kills_a_session_that_is_already_live(app):
    """The point of the epoch. Without it, 'revoke' means 'logged out whenever
    the cookie happens to expire'."""
    client_id = a_client_id(app)
    account_id = an_account(app, client_id)
    http = portal_session(app, account_id)
    assert http.get('/portal/').status_code == 200

    with app.app_context():
        portal_auth.revoke(db.session.get(Client, client_id))
        db.session.commit()

    response = http.get('/portal/')

    assert response.status_code == 302
    assert '/portal/login' in response.headers['Location']


def test_a_stale_epoch_in_the_session_is_refused(app):
    account_id = an_account(app)
    http = portal_session(app, account_id, epoch=0)

    assert http.get('/portal/').status_code == 302


# --- throttling ----------------------------------------------------------

def test_the_portal_login_locks_out_after_repeated_failures(app):
    an_account(app)
    http = app.test_client()

    for _ in range(portal_auth.MAX_FAILURES):
        http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                         'password': 'wrong'})

    http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                     'password': 'portal-pass-1234'})

    with http.session_transaction() as session:
        assert 'portal_account_id' not in session


def test_the_freelancer_login_locks_out_after_repeated_failures(app):
    """Hardening the new door and leaving the old one open is not a position
    worth defending - it is the same helper either way."""
    http = app.test_client()

    for _ in range(portal_auth.MAX_FAILURES):
        http.post('/login', data={'email': 'demo@gigledger.com', 'password': 'wrong'})

    http.post('/login', data={'email': 'demo@gigledger.com', 'password': 'demo1234'})

    with http.session_transaction() as session:
        assert '_user_id' not in session


def test_a_successful_login_clears_the_failure_count(app):
    an_account(app)
    http = app.test_client()
    for _ in range(portal_auth.MAX_FAILURES - 1):
        http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                         'password': 'wrong'})

    http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                     'password': 'portal-pass-1234'})
    http.post('/portal/logout')
    for _ in range(portal_auth.MAX_FAILURES - 1):
        http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                         'password': 'wrong'})
    http.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                     'password': 'portal-pass-1234'})

    with http.session_transaction() as session:
        assert 'portal_account_id' in session


# --- the off switch ------------------------------------------------------

def test_the_portal_can_be_switched_off_entirely(tmp_path, monkeypatch):
    """Disabled means the routes do not exist. A route that exists and refuses
    is still a surface, and still announces that the feature is there."""
    monkeypatch.setenv('PORTAL_ENABLED', '0')
    app = build_app(tmp_path, monkeypatch)

    assert [r for r in app.url_map.iter_rules() if r.endpoint.startswith('portal.')] == []
    assert app.test_client().get('/portal/login').status_code == 404


def test_the_portal_is_on_by_default(app):
    assert app.test_client().get('/portal/login').status_code == 200


# --- the freelancer's side of the invite ---------------------------------

def test_enabling_portal_access_shows_the_invite_link_once(app):
    client_id = a_client_id(app)
    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True

    body = http.post(f'/clients/{client_id}/portal/invite',
                     data={'email': 'billing@acmecorp.com'},
                     follow_redirects=True).get_data(as_text=True)

    assert '/portal/invite/' in body


def test_any_admin_can_invite_a_client(app):
    client_id = a_client_id(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = str(colleague_id)
        session['_fresh'] = True

    response = http.post(f'/clients/{client_id}/portal/invite',
                         data={'email': 'x@example.com'})

    assert response.status_code == 302
    with app.app_context():
        assert PortalInvite.query.filter_by(client_id=client_id).count() == 1
