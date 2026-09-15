"""Inventory purchases: one ledger line plus one linked asset row.

Recorded in docs/adr/0011. The purchase stays a Transaction so cash, lists,
filters and exports need no new arithmetic; the asset facts - quantity, unit
cost, consumable, project - live on InventoryItem, which is where piece 3's
movements will hang.
"""
import sqlite3
from datetime import datetime

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import (DEFAULT_EXPENSE_CATEGORIES, DEFAULT_INCOME_CATEGORIES,
                              DEFAULT_INVENTORY_CATEGORIES, EXPENSE, INCOME,
                              INVENTORY, INVENTORY_CATEGORY_GUIDANCE, KINDS,
                              InventoryItem, Project, RecurringTransaction,
                              Transaction, User, db)
from test_finance_characterization import (authenticated_client, build_app,
                                           demo_user_id)


@pytest.fixture
def app(tmp_path, monkeypatch):
    return build_app(tmp_path, monkeypatch)


# --- Vocabulary ------------------------------------------------------------

def test_there_are_seven_inventory_categories_each_with_guidance():
    assert len(DEFAULT_INVENTORY_CATEGORIES) == 7
    assert set(INVENTORY_CATEGORY_GUIDANCE) == set(DEFAULT_INVENTORY_CATEGORIES)
    assert all(INVENTORY_CATEGORY_GUIDANCE[c] for c in DEFAULT_INVENTORY_CATEGORIES)


def test_a_user_gets_the_default_inventory_categories(app):
    with app.app_context():
        user = User.query.get(demo_user_id(app))
        assert user.get_inventory_categories() == DEFAULT_INVENTORY_CATEGORIES
        assert user.get_inventory_categories() is not DEFAULT_INVENTORY_CATEGORIES


def test_all_categories_is_kind_aware(app):
    """The transactions filter wants all three lists; the recurring page,
    which cannot create an inventory row, wants two."""
    with app.app_context():
        user = User.query.get(demo_user_id(app))
        everything = user.get_all_categories()
        assert 'Seating' in everything and 'Software' in everything
        two = user.get_all_categories(kinds={INCOME, EXPENSE})
        assert 'Seating' not in two
        assert two == list(dict.fromkeys(
            DEFAULT_INCOME_CATEGORIES + DEFAULT_EXPENSE_CATEGORIES))


# --- The item --------------------------------------------------------------

def purchase(app, description='Sectional sofa', quantity=2, unit_cost=490.0,
             project_id=None, is_consumable=False, category='Seating',
             day=15):
    """One inventory purchase written directly: a Transaction and its item."""
    with app.app_context():
        uid = demo_user_id(app)
        tx = Transaction(user_id=uid, amount=-(quantity * unit_cost),
                         date=datetime(2026, 3, day, 12, 0), kind=INVENTORY,
                         category=category, description=description,
                         is_tax_deductible=False, source='manual')
        tx.inventory_item = InventoryItem(
            user_id=uid, project_id=project_id, quantity=quantity,
            unit_cost=unit_cost, is_consumable=is_consumable)
        db.session.add(tx)
        db.session.commit()
        return tx.id


def test_an_item_knows_its_total_and_its_location(app):
    tx_id = purchase(app)
    with app.app_context():
        item = Transaction.query.get(tx_id).inventory_item
        assert item.total_cost == 980.0
        assert item.location_label == 'General Inventory'
        assert item.usage_label == 'Reusable'
        assert item.transaction.description == 'Sectional sofa'


def test_an_item_on_a_project_names_the_project(app):
    with app.app_context():
        project = Project.query.filter_by(user_id=demo_user_id(app)).first()
        pid, name = project.id, project.name
    tx_id = purchase(app, project_id=pid, is_consumable=True)
    with app.app_context():
        item = Transaction.query.get(tx_id).inventory_item
        assert item.location_label == name
        assert item.usage_label == 'Consumable'
        assert item in Project.query.get(pid).inventory_items


def test_form_values_are_what_the_edit_modal_needs(app):
    tx_id = purchase(app, quantity=3, unit_cost=12.5, is_consumable=True)
    with app.app_context():
        item = Transaction.query.get(tx_id).inventory_item
        assert item.form_values == {
            'quantity': 3, 'unit_cost': 12.5, 'is_consumable': True,
            'project_id': None}


def test_deleting_the_purchase_deletes_the_item(app):
    tx_id = purchase(app)
    with app.app_context():
        db.session.delete(Transaction.query.get(tx_id))
        db.session.commit()
        assert InventoryItem.query.count() == 0


def test_deleting_a_project_returns_its_items_to_general_inventory(app):
    with app.app_context():
        pid = Project.query.filter_by(user_id=demo_user_id(app)).first().id
    tx_id = purchase(app, project_id=pid)
    with app.app_context():
        db.session.delete(Project.query.get(pid))
        db.session.commit()
        item = Transaction.query.get(tx_id).inventory_item
        assert item is not None
        assert item.project_id is None


# --- Migration -------------------------------------------------------------

def test_an_existing_users_table_gains_the_inventory_categories_column(tmp_path, monkeypatch):
    # Database schema from before custom_inventory_categories existed
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users ("
                 "id INTEGER PRIMARY KEY, "
                 "email VARCHAR(120) UNIQUE NOT NULL, "
                 "password_hash VARCHAR(128) NOT NULL, "
                 "default_tax_rate FLOAT DEFAULT 0.3, "
                 "currency VARCHAR(3) DEFAULT 'USD', "
                 "custom_income_categories TEXT DEFAULT '', "
                 "custom_expense_categories TEXT DEFAULT '', "
                 "theme VARCHAR(20) DEFAULT 'emerald', "
                 "dark_mode BOOLEAN DEFAULT 0, "
                 "business_name VARCHAR(200) DEFAULT '', "
                 "business_address TEXT DEFAULT '', "
                 "business_phone VARCHAR(50) DEFAULT '', "
                 "invoice_note TEXT DEFAULT 'Thank you for your business!', "
                 "invoice_prefix VARCHAR(10) DEFAULT 'INV', "
                 "next_invoice_number INTEGER DEFAULT 1, "
                 "created_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()
    create_app()  # a second startup must be a no-op

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    conn.close()
    assert 'custom_inventory_categories' in columns
