"""The explicit transaction kind.

The decision is recorded in docs/adr/0010. The point worth restating here: a
sign carries one bit, so classifying by `amount > 0` supports two kinds and no
more. `kind` is stored, and the sign of `amount` says only which direction the
money moved.
"""
import sqlite3

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
