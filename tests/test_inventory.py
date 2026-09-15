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


# --- add(): the input path ---------------------------------------------------

def add_purchase(client, **overrides):
    data = {'type': 'inventory', 'date': '2026-03-15', 'category': 'Seating',
            'description': 'Sectional sofa', 'quantity': '2',
            'unit_cost': '490', 'project_id': ''}
    data.update(overrides)
    return client.post('/transactions/add', data=data, follow_redirects=True)


def test_adding_an_inventory_purchase_writes_the_transaction_and_the_item(app):
    add_purchase(authenticated_client(app))
    with app.app_context():
        tx = Transaction.query.filter_by(description='Sectional sofa').one()
        assert tx.is_inventory
        assert tx.amount == -980.0
        assert tx.inventory_item.quantity == 2
        assert tx.inventory_item.unit_cost == 490
        assert tx.inventory_item.is_consumable is False
        assert tx.inventory_item.project_id is None


def test_the_posted_amount_is_ignored_for_inventory(app):
    """The modal hides the amount field, but the form is client-controlled."""
    add_purchase(authenticated_client(app), amount='5')
    with app.app_context():
        assert Transaction.query.filter_by(
            description='Sectional sofa').one().amount == -980.0


def test_inventory_is_never_tax_deductible(app):
    add_purchase(authenticated_client(app), is_tax_deductible='on')
    with app.app_context():
        assert Transaction.query.filter_by(
            description='Sectional sofa').one().is_tax_deductible is False


def test_an_inventory_purchase_can_be_bought_for_a_project(app):
    with app.app_context():
        pid = Project.query.filter_by(user_id=demo_user_id(app)).first().id
    add_purchase(authenticated_client(app), project_id=str(pid),
                 is_consumable='on')
    with app.app_context():
        item = Transaction.query.filter_by(
            description='Sectional sofa').one().inventory_item
        assert item.project_id == pid
        assert item.is_consumable is True


@pytest.mark.parametrize('bad', [
    {'quantity': '0'}, {'unit_cost': '-3'}, {'quantity': 'six'},
    {'quantity': ''}, {'unit_cost': ''}])
def test_a_non_positive_or_missing_quantity_or_unit_cost_is_refused(app, bad):
    body = add_purchase(authenticated_client(app), **bad).get_data(as_text=True)
    # Not the bare word 'required': every form input carries that attribute.
    assert ('must be greater than zero' in body
            or 'are required for an inventory purchase' in body)
    with app.app_context():
        assert Transaction.query.filter_by(description='Sectional sofa').count() == 0


def test_another_users_project_is_refused(app):
    with app.app_context():
        other = User(email='other@example.com', password_hash='x')
        db.session.add(other)
        db.session.flush()
        theirs = Project(user_id=other.id, name='Not yours')
        db.session.add(theirs)
        db.session.commit()
        pid = theirs.id
    add_purchase(authenticated_client(app), project_id=str(pid))
    with app.app_context():
        assert Transaction.query.filter_by(description='Sectional sofa').count() == 0


@pytest.mark.parametrize('kind,posted,stored', [
    ('income', '100', 100.0), ('income', '-100', 100.0),
    ('expense', '100', -100.0), ('expense', '-100', -100.0)])
def test_one_sign_rule_for_income_and_expense(app, kind, posted, stored):
    authenticated_client(app).post('/transactions/add', data={
        'type': kind, 'amount': posted, 'date': '2026-03-04',
        'category': 'x', 'description': 'sign probe'})
    with app.app_context():
        assert Transaction.query.filter_by(
            description='sign probe').one().amount == stored


# --- edit(): the blocker piece 1 recorded -----------------------------------

def edit_purchase(client, tx_id, **overrides):
    data = {'type': 'inventory', 'date': '2026-03-15', 'category': 'Seating',
            'description': 'Sectional sofa', 'quantity': '2',
            'unit_cost': '490', 'project_id': ''}
    data.update(overrides)
    return client.post(f'/transactions/edit/{tx_id}', data=data,
                       follow_redirects=True)


def test_editing_only_the_date_leaves_an_inventory_row_inventory(app):
    """The failure piece 1's appendix describes: the old modal posted
    type=expense for anything not income, and one date change moved the
    purchase into every cost total forever."""
    from gigledger.finance import calculate_monthly_summary
    with app.app_context():
        Transaction.query.delete()  # only our purchase in the ledger
        db.session.commit()
    tx_id = purchase(app)
    edit_purchase(authenticated_client(app), tx_id, date='2026-03-20')
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.is_inventory
        assert tx.date.day == 20
        assert tx.amount == -980.0
        _, expenses = calculate_monthly_summary(demo_user_id(app), 2026, 3)
    assert expenses == 0


def test_editing_an_inventory_row_updates_the_item_and_recomputes_the_amount(app):
    with app.app_context():
        pid = Project.query.filter_by(user_id=demo_user_id(app)).first().id
    tx_id = purchase(app)
    edit_purchase(authenticated_client(app), tx_id, quantity='3',
                  unit_cost='100', is_consumable='on', project_id=str(pid),
                  description='Three lamps', category='Lighting')
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.amount == -300.0
        assert tx.description == 'Three lamps'
        assert tx.category == 'Lighting'
        assert tx.inventory_item.quantity == 3
        assert tx.inventory_item.is_consumable is True
        assert tx.inventory_item.project_id == pid
        assert InventoryItem.query.count() == 1


def test_a_kind_change_out_of_inventory_is_refused_and_changes_nothing(app):
    tx_id = purchase(app)
    body = edit_purchase(authenticated_client(app), tx_id, type='expense',
                         amount='980', description='Now an expense',
                         date='2026-03-20').get_data(as_text=True)
    assert 'Delete and re-add' in body
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.is_inventory
        assert tx.description == 'Sectional sofa'
        assert tx.date.day == 15
        assert tx.inventory_item is not None


def test_a_kind_change_into_inventory_is_refused_and_changes_nothing(app):
    client = authenticated_client(app)
    client.post('/transactions/add', data={
        'type': 'expense', 'amount': '59.99', 'date': '2026-03-04',
        'category': 'Software', 'description': 'Adobe CC'})
    with app.app_context():
        tx_id = Transaction.query.filter_by(description='Adobe CC').one().id
    body = edit_purchase(client, tx_id, description='Adobe as furniture'
                         ).get_data(as_text=True)
    assert 'Delete and re-add' in body
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.is_expense
        assert tx.description == 'Adobe CC'
        assert tx.inventory_item is None


def test_an_invalid_inventory_edit_changes_nothing(app):
    tx_id = purchase(app)
    edit_purchase(authenticated_client(app), tx_id, quantity='0',
                  description='Should not land')
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.description == 'Sectional sofa'
        assert tx.amount == -980.0


def test_editing_an_expense_still_works_as_before(app):
    client = authenticated_client(app)
    client.post('/transactions/add', data={
        'type': 'expense', 'amount': '59.99', 'date': '2026-03-04',
        'category': 'Software', 'description': 'Adobe CC'})
    with app.app_context():
        tx_id = Transaction.query.filter_by(description='Adobe CC').one().id
    client.post(f'/transactions/edit/{tx_id}', data={
        'type': 'expense', 'amount': '60', 'date': '2026-03-05',
        'category': 'Software', 'description': 'Adobe CC',
        'is_tax_deductible': 'on'})
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert (tx.amount, tx.is_tax_deductible, tx.date.day) == (-60.0, True, 5)


def test_deleting_an_inventory_purchase_through_the_route_removes_the_item(app):
    tx_id = purchase(app)
    authenticated_client(app).post(f'/transactions/delete/{tx_id}')
    with app.app_context():
        assert Transaction.query.get(tx_id) is None
        assert InventoryItem.query.count() == 0
