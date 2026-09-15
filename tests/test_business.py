"""The one Business row and how it comes to exist.

Spec: docs/superpowers/specs/2026-09-15-one-business-per-install-design.md
"""
import sqlite3

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import Business, User, db
from tests.conftest import build_app


def test_business_get_returns_the_seeded_row(app):
    with app.app_context():
        business = Business.get()
        assert business.name == 'Demo Freelance Studio'
        assert business.invoice_prefix == 'INV'
        assert Business.query.count() == 1


def test_business_get_creates_a_default_row_when_none_exists(app):
    with app.app_context():
        Business.query.delete()
        db.session.commit()
        business = Business.get()
        assert business.id is not None
        assert business.default_tax_rate == 0.30
        assert business.currency == 'USD'
        assert Business.query.count() == 1


def test_business_get_is_idempotent(app):
    with app.app_context():
        assert Business.get().id == Business.get().id


def test_the_user_model_no_longer_carries_business_settings(app):
    for column in ('business_name', 'default_tax_rate', 'invoice_prefix',
                   'next_invoice_number', 'custom_income_categories'):
        assert not hasattr(User, column)


def test_invoice_numbering_lives_on_the_business(app):
    with app.app_context():
        business = Business.get()
        business.invoice_prefix = 'DSD'
        business.next_invoice_number = 7
        db.session.commit()
        assert business.get_next_invoice_number() == 'DSD-0007'
        assert Business.get().next_invoice_number == 8


def test_category_helpers_live_on_the_business(app):
    with app.app_context():
        business = Business.get()
        business.custom_income_categories = 'Design Fee,Procurement Fee'
        assert business.get_income_categories() == ['Design Fee', 'Procurement Fee']
        assert 'Software' in business.get_expense_categories()
        assert business.get_all_categories()[0] == 'Design Fee'


def _legacy_database(path):
    """A database as the previous release left it: business settings on users,
    no business table."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, email VARCHAR(120) NOT NULL UNIQUE,
            password_hash VARCHAR(128) NOT NULL,
            default_tax_rate FLOAT, currency VARCHAR(3),
            custom_income_categories TEXT, custom_expense_categories TEXT,
            custom_inventory_categories TEXT,
            theme VARCHAR(20), dark_mode BOOLEAN,
            business_name VARCHAR(200), business_address TEXT,
            business_phone VARCHAR(50), invoice_note TEXT,
            invoice_prefix VARCHAR(10), next_invoice_number INTEGER,
            created_at DATETIME);
        INSERT INTO users VALUES
          (1, 'first@example.com', 'x', 0.25, 'GBP', 'Design Fee', '', '',
           'ocean', 0, 'First Studio', '1 High St', '0207', 'Thanks', 'FS', 42,
           '2026-01-01 00:00:00'),
          (2, 'second@example.com', 'x', 0.30, 'USD', '', '', '',
           'emerald', 0, 'Second Studio', '', '', '', 'INV', 1,
           '2026-02-01 00:00:00');
    """)
    conn.commit()
    conn.close()


def test_migration_seeds_the_business_from_the_lowest_id_user(tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = tmp_path / 'legacy.db'
    _legacy_database(str(path))
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(path))

    app = create_app()

    with app.app_context():
        business = Business.query.one()
        assert business.name == 'First Studio'
        assert business.address == '1 High St'
        assert business.phone == '0207'
        assert business.default_tax_rate == 0.25
        assert business.currency == 'GBP'
        assert business.invoice_prefix == 'FS'
        assert business.next_invoice_number == 42
        assert business.invoice_note == 'Thanks'
        assert business.custom_income_categories == 'Design Fee'
        assert User.query.count() == 2


def test_migration_is_a_no_op_the_second_time(tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = tmp_path / 'legacy.db'
    _legacy_database(str(path))
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(path))

    create_app()
    app = create_app()

    with app.app_context():
        assert Business.query.count() == 1
        assert Business.query.one().name == 'First Studio'


def test_migration_adds_last_login_at_to_users(tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = tmp_path / 'legacy.db'
    _legacy_database(str(path))
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(path))

    app = create_app()

    with app.app_context():
        assert User.query.get(1).last_login_at is None


def test_the_seed_creates_the_business_row(app):
    with app.app_context():
        assert Business.query.count() == 1
        assert Business.query.one().name == 'Demo Freelance Studio'
