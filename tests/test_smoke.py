"""Startup smoke tests.

These guard the failure class that motivated the package restructure: the app
failing to import or resolving its database to the wrong place. Both fail at
launch rather than under test, so they are cheap to assert and expensive to
discover in production. See docs/adr/0001 and docs/adr/0002.
"""
import os

import gigledger.app


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_package_imports_by_its_declared_name():
    """The package is importable as `gigledger` regardless of the checkout
    directory's name — the property that hardcoding, deriving, or symlinking
    the package name all failed to provide."""
    assert gigledger.app.__name__ == 'gigledger.app'


def test_database_lives_in_repo_root_not_inside_the_package():
    """DB_PATH is asserted here unpatched — this is the test that must keep
    exercising the real resolution logic, not a throwaway path."""
    package_dir = os.path.dirname(os.path.abspath(gigledger.app.__file__))
    assert gigledger.app.DB_PATH == os.path.join(REPO_ROOT, 'gigledger.db')
    assert not gigledger.app.DB_PATH.startswith(package_dir + os.sep)


def test_app_factory_builds_and_agrees_with_the_migration_path(tmp_path, monkeypatch):
    """create_app() runs db.create_all(), _migrate_db() and _seed_demo_data()
    against DB_PATH, so this must point at a throwaway database rather than
    the real gigledger.db in the repo root, the way every other test file's
    build_app() does."""
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'smoke.db'))
    app = gigledger.app.create_app()
    assert app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite:///' + gigledger.app.DB_PATH
