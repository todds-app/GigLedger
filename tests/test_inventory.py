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
from gigledger.models import (Business, DEFAULT_EXPENSE_CATEGORIES,
                              DEFAULT_INCOME_CATEGORIES,
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


def test_the_business_gets_the_default_inventory_categories(app):
    with app.app_context():
        business = Business.get()
        assert business.get_inventory_categories() == DEFAULT_INVENTORY_CATEGORIES
        assert business.get_inventory_categories() is not DEFAULT_INVENTORY_CATEGORIES


def test_all_categories_is_kind_aware(app):
    """The transactions filter wants all three lists; the recurring page,
    which cannot create an inventory row, wants two."""
    with app.app_context():
        business = Business.get()
        everything = business.get_all_categories()
        assert 'Seating' in everything and 'Software' in everything
        two = business.get_all_categories(kinds={INCOME, EXPENSE})
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
    {'quantity': ''}, {'unit_cost': ''}, {'quantity': 'inf'},
    {'unit_cost': '1e999'}, {'quantity': 'nan'}])
def test_a_non_positive_or_missing_quantity_or_unit_cost_is_refused(app, bad):
    body = add_purchase(authenticated_client(app), **bad).get_data(as_text=True)
    # Not the bare word 'required': every form input carries that attribute.
    assert ('must be greater than zero' in body
            or 'are required for an inventory purchase' in body)
    with app.app_context():
        assert Transaction.query.filter_by(description='Sectional sofa').count() == 0


def test_a_project_created_by_another_admin_is_accepted(app):
    """Every admin works the same books (ADR-0014): a project one of them
    created is available to all of them."""
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
        tx = Transaction.query.filter_by(description='Sectional sofa').one()
        assert tx.inventory_item.project_id == pid


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
        _, expenses = calculate_monthly_summary(2026, 3)
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


def test_editing_can_turn_a_consumable_back_into_a_reusable(app):
    tx_id = purchase(app, is_consumable=True)
    edit_purchase(authenticated_client(app), tx_id)  # no is_consumable key posted
    with app.app_context():
        assert Transaction.query.get(tx_id).inventory_item.is_consumable is False


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


# --- Monthly commitment ------------------------------------------------------

# --- The transactions page ---------------------------------------------------

def test_the_type_filter_offers_inventory_and_filters_by_it(app):
    purchase(app)
    client = authenticated_client(app)
    page = client.get('/transactions').get_data(as_text=True)
    assert 'value="inventory"' in page  # the filter option and the radios
    filtered = client.get('/transactions?type=inventory').get_data(as_text=True)
    assert 'Sectional sofa' in filtered
    assert '1 transaction found' in filtered


def test_the_add_modal_offers_inventory_with_the_item_fields(app):
    page = authenticated_client(app).get('/transactions').get_data(as_text=True)
    for field in ('name="quantity"', 'name="unit_cost"', 'name="is_consumable"',
                  'name="project_id"', 'General Inventory'):
        assert field in page
    # The filter dropdown (autoescaped HTML) and the modal's JS (Flask's
    # tojson escapes & as &) both carry the inventory categories.
    assert 'Casegoods &amp; Storage' in page
    assert 'Casegoods \\u0026 Storage' in page


def test_the_edit_button_carries_the_item_for_an_inventory_row(app):
    purchase(app, quantity=2, unit_cost=490.0)
    page = authenticated_client(app).get('/transactions').get_data(as_text=True)
    # tojson|forceescape turns the quotes into &#34;
    assert '&#34;quantity&#34;: 2.0' in page
    assert '&#34;unit_cost&#34;: 490.0' in page


def test_the_html_export_shows_the_type_column(app):
    purchase(app)
    body = authenticated_client(app).get(
        '/transactions/export/pdf').get_data(as_text=True)
    assert '<th>Type</th>' in body
    assert '<td>Inventory</td>' in body


def test_monthly_commitment_counts_a_recurring_inventory_order(app):
    """A commitment is an obligation to pay, not a P&L category. Decided in
    the piece 2 spec; ADR-0010 lists it as the third kind-blind figure."""
    with app.app_context():
        uid = demo_user_id(app)
        RecurringTransaction.query.delete()
        db.session.add(RecurringTransaction(
            user_id=uid, description='Candles, monthly', amount=-120.0,
            kind=INVENTORY, category='Tabletop & Decorative Accessories',
            frequency='monthly', day_of_month=1, is_active=True,
            next_date=datetime(2099, 1, 1)))
        db.session.add(RecurringTransaction(
            user_id=uid, description='Hosting', amount=-30.0, kind=EXPENSE,
            frequency='monthly', day_of_month=1, is_active=True,
            next_date=datetime(2099, 1, 1)))
        db.session.add(RecurringTransaction(
            user_id=uid, description='Retainer', amount=500.0, kind=INCOME,
            frequency='monthly', day_of_month=1, is_active=True,
            next_date=datetime(2099, 1, 1)))
        db.session.commit()
    client = authenticated_client(app)
    assert '$150.00' in client.get('/recurring/').get_data(as_text=True)
    assert '$150.00' in client.get('/').get_data(as_text=True)


# --- The Inventory page ------------------------------------------------------

@pytest.fixture
def pool(app):
    """Two items on hand, one on a project."""
    with app.app_context():
        project = Project.query.filter_by(user_id=demo_user_id(app)).first()
        pid, pname = project.id, project.name
    purchase(app, description='Sectional sofa', quantity=2, unit_cost=490.0)
    purchase(app, description='Brass floor lamp', quantity=1, unit_cost=220.0,
             category='Lighting', day=16)
    purchase(app, description='Throw pillows', quantity=6, unit_cost=25.0,
             category='Soft Goods & Textiles', project_id=pid,
             is_consumable=True, day=17)
    return app, pid, pname


def test_the_inventory_page_totals_the_pool(pool):
    app, _, pname = pool
    page = authenticated_client(app).get('/inventory/').get_data(as_text=True)
    assert '$1,350.00' in page   # asset value: 980 + 220 + 150
    assert '$1,200.00' in page   # on hand
    assert '$150.00' in page     # on projects
    for text in ('Sectional sofa', 'Brass floor lamp', 'Throw pillows',
                 'General Inventory', pname, 'Consumable', 'Reusable'):
        assert text in page


def test_the_inventory_page_filters_by_project_and_category(pool):
    app, pid, _ = pool
    client = authenticated_client(app)
    general = client.get('/inventory/?project=general').get_data(as_text=True)
    assert 'Sectional sofa' in general and 'Throw pillows' not in general
    on_project = client.get(f'/inventory/?project={pid}').get_data(as_text=True)
    assert 'Throw pillows' in on_project and 'Sectional sofa' not in on_project
    lighting = client.get('/inventory/?category=Lighting').get_data(as_text=True)
    assert 'Brass floor lamp' in lighting and 'Sectional sofa' not in lighting
    # The stats describe the whole pool, not the filtered list.
    assert '$1,350.00' in lighting


def test_the_inventory_page_shows_items_entered_by_every_admin(pool):
    app, _, _ = pool
    with app.app_context():
        other = User(email='other@example.com', password_hash='x')
        db.session.add(other)
        db.session.flush()
        tx = Transaction(user_id=other.id, amount=-999.0,
                         date=datetime(2026, 3, 1), kind=INVENTORY,
                         category='Seating', description='Theirs')
        tx.inventory_item = InventoryItem(user_id=other.id, quantity=1,
                                          unit_cost=999.0)
        db.session.add(tx)
        db.session.commit()
    page = authenticated_client(app).get('/inventory/').get_data(as_text=True)
    assert 'Theirs' in page
    # The pool's own $1,350 (980 + 220 + 150) plus their $999 item: the total
    # now counts every admin's purchases, not just the signed-in one's.
    assert '$2,349.00' in page


def test_the_inventory_page_has_an_empty_state_and_the_edit_modal(app):
    page = authenticated_client(app).get('/inventory/').get_data(as_text=True)
    assert 'No inventory yet' in page
    assert 'id="editModal"' in page


def test_the_nav_links_to_inventory(app):
    page = authenticated_client(app).get('/transactions').get_data(as_text=True)
    assert 'href="/inventory/"' in page


# --- Project detail ----------------------------------------------------------

def test_project_detail_lists_the_items_bought_for_it(pool):
    app, pid, _ = pool
    page = authenticated_client(app).get(f'/projects/{pid}').get_data(as_text=True)
    assert 'Throw pillows' in page
    assert 'Sectional sofa' not in page
    assert '$150.00' in page


def test_project_detail_without_items_says_so_in_one_line(app):
    with app.app_context():
        pid = Project.query.filter_by(user_id=demo_user_id(app)).first().id
    page = authenticated_client(app).get(f'/projects/{pid}').get_data(as_text=True)
    assert 'No inventory bought for this project' in page


# --- Settings ----------------------------------------------------------------

def test_settings_shows_the_inventory_categories_with_guidance(app):
    page = authenticated_client(app).get('/settings').get_data(as_text=True)
    assert 'Inventory Categories' in page
    assert 'Casegoods &amp; Storage' in page
    assert 'Beds, nightstands, dressers' in page


def test_an_inventory_category_can_be_added_and_removed(app):
    client = authenticated_client(app)
    client.post('/settings/categories/inventory/add', data={'category_name': 'Appliances'})
    with app.app_context():
        assert 'Appliances' in Business.get().get_inventory_categories()
    client.post('/settings/categories/inventory/delete', data={'category_name': 'Appliances'})
    with app.app_context():
        assert 'Appliances' not in Business.get().get_inventory_categories()


@pytest.mark.parametrize('kind,default', [
    ('income', 'Client Payment'), ('expense', 'Software'), ('inventory', 'Seating')])
def test_a_default_category_cannot_be_removed(app, kind, default):
    body = authenticated_client(app).post(
        f'/settings/categories/{kind}/delete', data={'category_name': default},
        follow_redirects=True).get_data(as_text=True)
    assert 'Default categories' in body
    with app.app_context():
        business = Business.get()
        assert default in business.get_all_categories()


def test_reset_clears_the_inventory_list_too(app):
    client = authenticated_client(app)
    client.post('/settings/categories/inventory/add', data={'category_name': 'Appliances'})
    client.post('/settings/categories/reset')
    with app.app_context():
        business = Business.get()
        assert business.custom_inventory_categories == ''
        assert business.get_inventory_categories() == DEFAULT_INVENTORY_CATEGORIES


def test_the_recurring_page_does_not_offer_inventory_categories(app):
    page = authenticated_client(app).get('/recurring/').get_data(as_text=True)
    assert 'Casegoods' not in page


# --- Recurring cannot mint an itemless inventory row ------------------------

def test_the_recurring_route_refuses_the_inventory_kind(app):
    """The recurring form offers two kinds; a crafted POST must not create a
    third. A recurring inventory row would generate inventory transactions
    with no InventoryItem behind them."""
    authenticated_client(app).post('/recurring/add', data={
        'type': 'inventory', 'description': 'Candles, monthly',
        'amount': '120', 'category': 'Seating', 'frequency': 'monthly',
        'day_of_month': '1'})
    with app.app_context():
        rt = RecurringTransaction.query.filter_by(description='Candles, monthly').one()
        assert rt.is_expense
        assert rt.amount == -120.0


def test_editing_an_itemless_inventory_row_is_refused_not_crashed(app):
    with app.app_context():
        uid = demo_user_id(app)
        tx = Transaction(user_id=uid, amount=-50.0, date=datetime(2026, 3, 1),
                         kind=INVENTORY, category='Seating',
                         description='Orphan', is_tax_deductible=False,
                         source='manual')
        db.session.add(tx)
        db.session.commit()
        tx_id = tx.id
    body = edit_purchase(authenticated_client(app), tx_id, description='Renamed'
                         ).get_data(as_text=True)
    assert 'no item behind it' in body
    with app.app_context():
        tx = Transaction.query.get(tx_id)
        assert tx.description == 'Orphan'
        assert tx.amount == -50.0
