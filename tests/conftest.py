"""Shared fixtures.

`SEED_DEMO=1` is set for every test because most existing fixtures reach for
the seeded demo rows (project 1, client 1, user 1). The one test that checks
the seed stays off by default (tests/test_team.py) deletes the variable again.
"""
import pytest

import gigledger.app
import gigledger.documents
from gigledger.app import create_app


@pytest.fixture(autouse=True)
def _seed_demo_for_tests(monkeypatch):
    monkeypatch.setenv('SEED_DEMO', '1')


def build_app(tmp_path, monkeypatch, **config):
    """An app backed by a throwaway database and a throwaway upload root."""
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


def login_as(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
    return client


@pytest.fixture
def admin_client(app):
    """A test client signed in as the seeded demo admin (user 1)."""
    return login_as(app)
