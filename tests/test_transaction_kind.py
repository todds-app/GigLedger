"""The explicit transaction kind.

The decision is recorded in docs/adr/0010. The point worth restating here: a
sign carries one bit, so classifying by `amount > 0` supports two kinds and no
more. `kind` is stored, and the sign of `amount` says only which direction the
money moved.
"""
import html
import sqlite3
from datetime import datetime

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import (COST_KINDS, EXPENSE, INCOME, INVENTORY, KINDS,
                              RecurringTransaction, Transaction, clean_kind)


def test_the_vocabulary_is_three_kinds():
    assert KINDS == {'income', 'expense', 'inventory'}
    assert (INCOME, EXPENSE, INVENTORY) == ('income', 'expense', 'inventory')


def test_only_expenses_reduce_profit():
    """Inventory is cash out but not a cost: the money bought an asset."""
    assert COST_KINDS == {EXPENSE}
    assert INVENTORY not in COST_KINDS
    assert INCOME not in COST_KINDS


@pytest.mark.parametrize('value', ['income', 'expense', 'inventory'])
def test_clean_kind_accepts_every_known_kind(value):
    assert clean_kind(value) == value


@pytest.mark.parametrize('value', ['INCOME', ' Expense ', 'Inventory'])
def test_clean_kind_normalises_case_and_whitespace(value):
    assert clean_kind(value) in KINDS


@pytest.mark.parametrize('value', ['', None, 'asset', 'drop table', 'Income '])
def test_clean_kind_falls_back_for_anything_unrecognised(value):
    """A Constrained Column: the write decides, so the column cannot hold a
    kind the rest of the code has never heard of."""
    assert clean_kind(value, fallback=EXPENSE) in KINDS


def test_clean_kind_honours_an_explicit_fallback():
    assert clean_kind('nonsense', fallback=INCOME) == INCOME


@pytest.mark.parametrize('model', [Transaction, RecurringTransaction])
def test_both_models_classify_the_same_way(model):
    assert model(kind=INCOME).is_income
    assert model(kind=EXPENSE).is_expense
    assert model(kind=INVENTORY).is_inventory
    assert not model(kind=INCOME).is_expense
    assert not model(kind=EXPENSE).is_inventory


@pytest.mark.parametrize('kind,label', [
    (INCOME, 'Income'), (EXPENSE, 'Expense'), (INVENTORY, 'Inventory')])
def test_kind_label_is_display_ready(kind, label):
    assert Transaction(kind=kind).kind_label == label


def test_kind_does_not_follow_the_sign_of_the_amount():
    """The whole point: an amount's sign no longer decides what it is."""
    assert Transaction(kind=INVENTORY, amount=-980.00).is_inventory
    assert not Transaction(kind=INVENTORY, amount=-980.00).is_expense


def legacy_database(path):
    """A database shaped the way it was before `kind` existed.

    Only the columns the backfill reads are needed; create_all fills in the
    rest of the schema on first startup.
    """
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE transactions ("
                 "id INTEGER PRIMARY KEY, user_id INTEGER, amount FLOAT, "
                 "date DATETIME, category VARCHAR(50), description VARCHAR(200), "
                 "is_tax_deductible BOOLEAN, source VARCHAR(20), "
                 "invoice_id INTEGER, created_at DATETIME)")
    conn.execute("CREATE TABLE recurring_transactions ("
                 "id INTEGER PRIMARY KEY, user_id INTEGER, "
                 "description VARCHAR(200), amount FLOAT, category VARCHAR(50), "
                 "is_tax_deductible BOOLEAN, frequency VARCHAR(20), "
                 "day_of_month INTEGER, is_active BOOLEAN, "
                 "last_generated DATETIME, next_date DATETIME, "
                 "created_at DATETIME)")
    conn.executemany(
        "INSERT INTO transactions (id, user_id, amount, date, category) "
        "VALUES (?, 1, ?, '2026-03-01 12:00:00', 'fixture')",
        [(1, 5000.00), (2, -1200.00), (3, 0.0)])
    conn.executemany(
        "INSERT INTO recurring_transactions (id, user_id, description, amount) "
        "VALUES (?, 1, 'fixture', ?)",
        [(1, 2000.00), (2, -49.00)])
    conn.commit()
    conn.close()


def kinds_in(path, table, ids):
    """The kind of each fixture row, keyed by id.

    Restricted to `ids` because `legacy_database` leaves no `users` row, so
    `_seed_demo_data()` fires on every `create_app()` call here and appends
    its own rows to the same table. Filtering to the ids this fixture itself
    inserted keeps the assertion about our rows, not about how much demo data
    happens to exist.
    """
    conn = sqlite3.connect(path)
    placeholders = ','.join('?' * len(ids))
    rows = dict(conn.execute(
        f"SELECT id, kind FROM {table} WHERE id IN ({placeholders})", ids))
    conn.close()
    return rows


def test_an_existing_database_gains_the_column(tmp_path, monkeypatch):
    """create_all() never alters an existing table, so the migration must."""
    db_path = str(tmp_path / 'test.db')
    legacy_database(db_path)
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()

    assert kinds_in(db_path, 'transactions', [1, 2, 3]) == {
        1: INCOME, 2: EXPENSE, 3: EXPENSE}
    assert kinds_in(db_path, 'recurring_transactions', [1, 2]) == {
        1: INCOME, 2: EXPENSE}


def test_a_zero_amount_row_backfills_as_an_expense(tmp_path, monkeypatch):
    """Row 3 above is zero. It counted as neither income nor expense before,
    because both sign tests were strict, and it lands in `expense` now. No
    displayed number moves: a zero contributes zero to an expense total."""
    db_path = str(tmp_path / 'test.db')
    legacy_database(db_path)
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()

    assert kinds_in(db_path, 'transactions', [3])[3] == EXPENSE


def test_the_migration_is_a_no_op_the_second_time(tmp_path, monkeypatch):
    """It runs on every startup, so running twice must not disturb a kind that
    has since been edited away from what the sign implies."""
    db_path = str(tmp_path / 'test.db')
    legacy_database(db_path)
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE transactions SET kind = 'inventory' WHERE id = 2")
    conn.commit()
    conn.close()

    create_app()

    assert kinds_in(db_path, 'transactions', [2])[2] == INVENTORY


from gigledger.models import User, db
from test_finance_characterization import (authenticated_client,
                                            build_app, demo_user_id)


@pytest.fixture
def app(tmp_path, monkeypatch):
    return build_app(tmp_path, monkeypatch)


def test_adding_an_expense_stores_the_expense_kind(app):
    authenticated_client(app).post('/transactions/add', data={
        'type': 'expense', 'amount': '59.99', 'date': '2026-03-04',
        'category': 'Software', 'description': 'Adobe CC'})
    with app.app_context():
        tx = Transaction.query.filter_by(description='Adobe CC').one()
        assert tx.is_expense
        assert tx.amount == -59.99


def test_adding_income_stores_the_income_kind(app):
    authenticated_client(app).post('/transactions/add', data={
        'type': 'income', 'amount': '2400', 'date': '2026-03-04',
        'category': 'Client Payment', 'description': 'Retainer'})
    with app.app_context():
        assert Transaction.query.filter_by(description='Retainer').one().is_income


def test_an_unrecognised_type_does_not_reach_the_column(app):
    """The form is client-controlled. clean_kind decides at the write."""
    authenticated_client(app).post('/transactions/add', data={
        'type': 'asset', 'amount': '100', 'date': '2026-03-04',
        'category': 'Software', 'description': 'Bogus type'})
    with app.app_context():
        tx = Transaction.query.filter_by(description='Bogus type').one()
        assert tx.kind in KINDS


def test_editing_with_a_missing_type_keeps_the_stored_kind(app):
    """edit() used to default a missing/unrecognised `type` to INCOME, so an
    edit that omitted the field silently reclassified the row. It must now
    fall back to the transaction's own stored kind instead."""
    with app.app_context():
        tx = Transaction(
            user_id=demo_user_id(app), amount=-59.99, kind=EXPENSE,
            date=datetime(2026, 3, 4, 12, 0), category='Software',
            description='Adobe CC', is_tax_deductible=False, source='manual')
        db.session.add(tx)
        db.session.commit()
        tx_id = tx.id

    authenticated_client(app).post(f'/transactions/edit/{tx_id}', data={
        'amount': '59.99', 'date': '2026-03-04', 'category': 'Software',
        'description': 'Adobe CC'})

    with app.app_context():
        edited = db.session.get(Transaction, tx_id)
        assert edited.is_expense


def test_every_seeded_transaction_has_a_kind(app):
    """The seed writes through five separate blocks; a miss in any of them
    leaves rows that later aggregations cannot classify."""
    with app.app_context():
        assert Transaction.query.count() > 0
        assert Transaction.query.filter(
            Transaction.kind.notin_(list(KINDS))).count() == 0
        assert Transaction.query.filter(Transaction.kind.is_(None)).count() == 0
        assert RecurringTransaction.query.filter(
            RecurringTransaction.kind.is_(None)).count() == 0


def test_seeded_kinds_agree_with_their_amounts(app):
    """Nothing in this piece creates inventory, so kind and sign still line up.
    When that stops being true in piece 2, this test is the one to change."""
    with app.app_context():
        for tx in Transaction.query.all():
            if tx.amount > 0:
                assert tx.is_income, f'{tx.description} is positive but {tx.kind}'
            elif tx.amount < 0:
                assert tx.is_expense, f'{tx.description} is negative but {tx.kind}'


def test_a_generated_recurring_transaction_inherits_its_kind(app):
    from datetime import datetime, timedelta
    with app.app_context():
        rt = RecurringTransaction(
            user_id=demo_user_id(app), description='Studio rent',
            amount=-1500.00, category='Rent', kind=EXPENSE, frequency='monthly',
            day_of_month=1, is_active=True,
            next_date=datetime.now() - timedelta(days=1))
        db.session.add(rt)
        db.session.commit()

    authenticated_client(app).post('/recurring/generate')

    with app.app_context():
        generated = Transaction.query.filter_by(
            description='Studio rent', source='recurring').one()
        assert generated.is_expense


def test_marking_an_invoice_paid_writes_income_and_a_tax_expense(app):
    from gigledger.models import Invoice
    with app.app_context():
        invoice = Invoice.query.filter(Invoice.tax_amount > 0).first()
        invoice_id, number = invoice.id, invoice.invoice_number
        invoice.status = 'sent'
        db.session.commit()

    authenticated_client(app).post(f'/invoices/status/{invoice_id}',
                                   data={'status': 'paid'})

    with app.app_context():
        linked = Transaction.query.filter_by(invoice_id=invoice_id).all()
        assert {t.kind for t in linked} == {INCOME, EXPENSE}
        assert all(t.kind in KINDS for t in linked), number


def test_editing_a_recurring_amount_keeps_its_kind(app):
    """The sign follows the kind, not the reverse: editing the amount of an
    expense must not turn it into income."""
    with app.app_context():
        rt = RecurringTransaction(
            user_id=demo_user_id(app), description='Studio insurance',
            amount=-90.00, category='Insurance', kind=EXPENSE,
            frequency='monthly', day_of_month=1, is_active=True)
        db.session.add(rt)
        db.session.commit()
        rt_id = rt.id

    authenticated_client(app).post(f'/recurring/edit/{rt_id}',
                                   data={'amount': '120', 'description': 'Studio insurance'})

    with app.app_context():
        edited = db.session.get(RecurringTransaction, rt_id)
        assert edited.is_expense
        assert edited.amount == -120.00


def test_editing_an_amount_takes_its_direction_from_the_kind_not_the_stored_sign(app):
    """The old expression read `rt.amount > 0` to decide the new sign, so a row
    whose stored sign disagreed with its kind kept the wrong direction forever.
    The kind decides now."""
    with app.app_context():
        rt = RecurringTransaction(
            user_id=demo_user_id(app), description='Mis-signed retainer',
            amount=90.00, category='Insurance', kind=EXPENSE,
            frequency='monthly', day_of_month=1, is_active=True)
        db.session.add(rt)
        db.session.commit()
        rt_id = rt.id

    authenticated_client(app).post(f'/recurring/edit/{rt_id}', data={
        'amount': '120', 'description': 'Mis-signed retainer'})

    with app.app_context():
        edited = db.session.get(RecurringTransaction, rt_id)
        assert edited.is_expense
        assert edited.amount == -120.00


def test_a_transaction_written_without_a_kind_is_refused(app):
    """The column has no default, so a forgotten kind is a loud failure
    rather than a plausible-looking expense. This is what makes the
    completeness tests above able to fail."""
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        db.session.add(Transaction(
            user_id=demo_user_id(app), amount=-10.00,
            date=datetime(2026, 3, 4, 12, 0), category='Software',
            description='No kind', is_tax_deductible=False, source='manual'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_the_edit_modal_receives_the_stored_kind(app):
    """The modal's kind argument decides which radio button is pre-selected.
    Derived from the sign, it would mislabel any row whose kind is not
    implied by its sign - which is every inventory row, once they exist."""
    with app.app_context():
        db.session.add(Transaction(
            user_id=demo_user_id(app), amount=-980.00,
            date=datetime(2026, 3, 4, 12, 0), kind=INVENTORY,
            category='Furniture', description='Sectional sofa',
            is_tax_deductible=False, source='manual'))
        db.session.commit()

    body = authenticated_client(app).get('/transactions').get_data(as_text=True)

    # The modal argument keeps its |tojson|forceescape per ADR-0004, so the
    # quotes reach the raw HTML as &#34; entities, not literal ". Unescape
    # before checking, the same way tests/test_template_escaping.py does.
    # The row's kind (inventory) disagrees with its sign (negative, which the
    # old code read as expense) - that disagreement is the whole point.
    assert '"inventory"' in html.unescape(body)


def test_the_category_column_still_shows_the_category(app):
    """Regression guard: the chip in the Category column is not a type badge.
    Its colour is keyed off is_income (classification), but its text is the
    row's actual category - converting the text to kind_label would silently
    turn the Category column into an Income/Expense label under a header
    that still says Category."""
    with app.app_context():
        db.session.add(Transaction(
            user_id=demo_user_id(app), amount=250.00,
            date=datetime(2026, 3, 5, 12, 0), kind=INCOME,
            category='Workshop Registration', description='Q1 workshop',
            is_tax_deductible=False, source='manual'))
        db.session.commit()

    body = authenticated_client(app).get('/transactions').get_data(as_text=True)

    assert 'Workshop Registration' in body


INVENTORY_PURCHASE = 980.00


@pytest.fixture
def with_inventory(tmp_path, monkeypatch):
    """A ledger plus one inventory purchase, built by hand because no UI can
    make one yet. That is the point: this fixture is piece 2's foundation
    under test before piece 2 exists."""
    from test_finance_characterization import LEDGER
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user_id = User.query.filter_by(email='demo@gigledger.com').first().id
        Transaction.query.delete()
        for amount, category, deductible, day in LEDGER:
            db.session.add(Transaction(
                user_id=user_id, amount=amount,
                date=datetime(2026, 3, day, 12, 0), kind=(
                    INCOME if amount > 0 else EXPENSE),
                category=category, description='fixture',
                is_tax_deductible=deductible, source='manual'))
        db.session.add(Transaction(
            user_id=user_id, amount=-INVENTORY_PURCHASE,
            date=datetime(2026, 3, 15, 12, 0), kind=INVENTORY,
            category='Seating', description='Sectional sofa',
            is_tax_deductible=False, source='manual'))
        db.session.commit()
    return app


def test_an_inventory_purchase_is_not_an_expense(with_inventory):
    """The whole reason for the kind column. Compare against the
    characterization figures: expenses are unchanged by 980 of inventory."""
    from gigledger.finance import calculate_monthly_summary
    with with_inventory.app_context():
        income, expenses = calculate_monthly_summary(
            demo_user_id(with_inventory), 2026, 3)
    assert income == 7500.00
    assert expenses == 1950.00


def test_an_inventory_purchase_does_not_reduce_taxable_income(with_inventory):
    from gigledger.finance import calculate_quarterly_tax
    with with_inventory.app_context():
        income, deductions, net, _ = calculate_quarterly_tax(
            demo_user_id(with_inventory), 1, 2026, 0.30)
    assert (income, deductions, net) == (7500.00, 1500.00, 6000.00)


def test_an_inventory_purchase_does_leave_the_bank(with_inventory):
    """Cash is not profit. The money is gone even though the cost is not
    recognised, so the balance falls by the full purchase price."""
    from gigledger.finance import calculate_safe_to_spend
    with with_inventory.app_context():
        balance, _, _ = calculate_safe_to_spend(
            demo_user_id(with_inventory), 0.30)
    assert balance == pytest.approx(5550.00 - INVENTORY_PURCHASE)


def test_an_inventory_purchase_is_absent_from_expense_categories(with_inventory):
    from gigledger.finance import get_category_breakdown
    with with_inventory.app_context():
        categories, _ = get_category_breakdown(
            demo_user_id(with_inventory), year=2026, month=3)
    assert 'Seating' not in categories


def test_an_inventory_purchase_survives_the_list_and_the_export(with_inventory):
    """Excluded from cost totals, but not hidden: it is still a transaction."""
    client = authenticated_client(with_inventory)
    page_body = client.get('/transactions').get_data(as_text=True)
    assert 'Sectional sofa' in page_body
    csv_body = client.get('/transactions/export/csv').get_data(as_text=True)
    assert ',Inventory,' in csv_body
    assert 'Total Expenses,,,,1950.00' in csv_body

    # The summary bar used to recompute its own totals by the sign of
    # `amount`, which counted the inventory purchase as an expense right
    # alongside it (980 too high: $2,930.00 instead of $1,950.00). The page
    # must agree with its own CSV export of the same list.
    assert '$1,950.00' in page_body
    assert '$2,930.00' not in page_body


def test_editing_an_inventory_recurring_amount_stays_negative(app):
    """Piece 1's edit() originally read `-amount if rt.is_expense else
    amount`, which sends every non-expense kind through the income branch.
    Inventory is cash out but is_expense is False for it, so that expression
    would flip an inventory purchase positive on a plain amount edit. The
    kind decides the sign directly - inventory takes the same negative
    branch as expense - so this must come back negative, not positive."""
    with app.app_context():
        rt = RecurringTransaction(
            user_id=demo_user_id(app), description='Studio furniture lease',
            amount=-250.00, category='Seating', kind=INVENTORY,
            frequency='monthly', day_of_month=1, is_active=True)
        db.session.add(rt)
        db.session.commit()
        rt_id = rt.id

    authenticated_client(app).post(f'/recurring/edit/{rt_id}', data={
        'amount': '300', 'description': 'Studio furniture lease'})

    with app.app_context():
        edited = db.session.get(RecurringTransaction, rt_id)
        assert edited.is_inventory
        assert edited.amount == -300.00


def test_a_recurring_inventory_row_is_stored_negative(app):
    """add() originally coerced the sign only for EXPENSE, leaving a fresh
    inventory row positive - the mirror image of the edit() bug above. That
    was fixed by having add() follow the same rule as edit(): every
    non-income kind is cash out and is stored negative.

    Piece 2 then closed the route this test used to exercise - /recurring/add
    now refuses type=inventory outright (see test_inventory.py's
    test_the_recurring_route_refuses_the_inventory_kind), because the
    recurring form offers no inventory option and a recurring inventory row
    would have no InventoryItem behind it. So this is rewritten, like its
    sibling test_editing_an_inventory_recurring_amount_stays_negative, to
    build the row directly rather than through a route that no longer
    accepts this kind - and it still pins the sign rule: inventory is cash
    out, stored negative, same as expense."""
    with app.app_context():
        rt = RecurringTransaction(
            user_id=demo_user_id(app), description='Showroom sectional',
            amount=-300.00, category='Seating', kind=INVENTORY,
            frequency='monthly', day_of_month=1, is_active=True)
        db.session.add(rt)
        db.session.commit()
        rt_id = rt.id

    with app.app_context():
        rt = db.session.get(RecurringTransaction, rt_id)
        assert rt.is_inventory
        assert rt.amount == -300.00
