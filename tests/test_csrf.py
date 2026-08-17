"""CSRF enforcement tests.

The important test is `test_every_unsafe_route_rejects_a_tokenless_post`, which
discovers routes by walking the url_map rather than listing them. A route added
later is covered without anyone remembering to write a test for it, and a
deliberate exemption has to be added to EXPECTED_EXEMPT below, where it shows up
in review. See docs/adr/0003.

Two things this file is careful about:

1. **It authenticates.** Probing as an anonymous user is nearly worthless here:
   35 of the 37 unsafe routes are @login_required, so they redirect to /login
   whether or not CSRF is enforced, and the test would pass with protection
   switched off. `test_the_probe_is_not_vacuous` pins that down.
2. **It uses a throwaway database.** create_app() otherwise resolves to the real
   gigledger.db, and a *failing* run - one where a route wrongly acts on the
   request - would write to the user's data.
"""
import pytest

import gigledger.app
from gigledger.app import create_app


# Routes deliberately not protected. Empty by design: an entry here is a
# decision that needs justifying in review, not a convenience.
EXPECTED_EXEMPT: set[str] = set()

UNSAFE = {'POST', 'PUT', 'PATCH', 'DELETE'}


def build_app(tmp_path, monkeypatch, **config):
    """An app backed by a throwaway database, never the real one."""
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key', **config)
    app.login_manager.session_protection = None
    return app


@pytest.fixture
def app(tmp_path, monkeypatch):
    return build_app(tmp_path, monkeypatch)


def authenticated_client(app):
    """Log in past @login_required so the CSRF check is what we actually probe."""
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = '1'  # the seeded demo user
        session['_fresh'] = True
    return client


def routes_that_acted(app, client):
    """Every unsafe route that responded to a token-less request as though it
    were legitimate."""
    acted = []
    for rule in app.url_map.iter_rules():
        if not rule.methods & UNSAFE or rule.endpoint in EXPECTED_EXEMPT:
            continue
        method = next(iter(rule.methods & UNSAFE))
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace(f'<int:{arg}>', '1').replace(f'<{arg}>', '1')
        response = client.open(path, method=method, data={'probe': 'x'})
        if response.status_code not in (400, 401, 403):
            acted.append(f'{method} {path} -> {response.status_code} ({rule.endpoint})')
    return acted


def test_csrf_protection_is_active(app):
    assert app.config['WTF_CSRF_ENABLED'] is True
    # Referer checking on HTTPS stays on; see ADR-0003 for the proxy caveat.
    assert app.config.get('WTF_CSRF_SSL_STRICT', True) is True


def test_every_unsafe_route_rejects_a_tokenless_post(app):
    acted = routes_that_acted(app, authenticated_client(app))
    assert not acted, 'Routes acted on a request with no CSRF token:\n' + '\n'.join(acted)


def test_the_probe_is_not_vacuous(tmp_path, monkeypatch):
    """Guards the test above against passing for the wrong reason.

    If the probe stopped exercising the CSRF layer - because it lost its login,
    or because the routes started refusing for some unrelated reason - the test
    above would still pass. So: with protection explicitly disabled, the same
    probe must find routes that act. If this fails, the test above proves
    nothing."""
    app = build_app(tmp_path, monkeypatch, WTF_CSRF_ENABLED=False)
    acted = routes_that_acted(app, authenticated_client(app))
    assert len(acted) > 20, (
        f'Only {len(acted)} routes acted with CSRF disabled; the probe is not '
        f'reaching the handlers, so the enforcement test is vacuous.'
    )


def test_logout_is_not_reachable_by_get(app):
    """A state-changing GET is not covered by CSRF protection at all."""
    rule = next(r for r in app.url_map.iter_rules() if r.endpoint == 'auth.logout')
    assert 'GET' not in rule.methods
    assert app.test_client().get('/logout').status_code == 405


def test_no_state_changing_route_is_reachable_by_a_safe_method(app):
    """Guards the class of bug that GET /logout was: mutation behind a method
    CSRF protection does not cover."""
    import inspect
    import re
    offenders = []
    for rule in app.url_map.iter_rules():
        if rule.methods & UNSAFE:
            continue
        view = app.view_functions.get(rule.endpoint)
        try:
            source = inspect.getsource(view)
        except (OSError, TypeError):
            continue
        if re.search(r'db\.session\.(commit|add|delete)\b|logout_user\(|login_user\(', source):
            offenders.append(f'{rule.rule} ({rule.endpoint})')
    assert not offenders, 'Safe-method routes that mutate state:\n' + '\n'.join(offenders)
