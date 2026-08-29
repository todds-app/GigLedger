# Explicit Transaction Kind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace sign-derived transaction classification with an explicit `kind` column, so a third kind (`inventory`) becomes representable without changing any number the app displays today.

**Architecture:** A `kind` column on `Transaction` and `RecurringTransaction`, backfilled from the sign of `amount` by the existing `_migrate_db` helper. One seam in `models.py` owns what each kind means (`KINDS`, `COST_KINDS`, `clean_kind`, and a `KindMixin` providing `is_income` / `is_expense` / `is_inventory` / `kind_label`). Every site that currently compares an amount to zero to decide *what a transaction is* asks the seam instead. Amounts stay signed, so every `sum(amount)` keeps working.

**Tech Stack:** Python 3.13, Flask, Flask-SQLAlchemy, SQLite, pytest. Existing venv at `venv/`; run tests with `venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-08-29-transaction-kind-design.md`

## Global Constraints

- **No displayed number may change.** Income and expense behave exactly as today. Task 1 pins this and every later task must keep it passing.
- **Amounts stay signed.** Do not convert to magnitude-plus-direction.
- **Cash sums stay kind-blind.** `calculate_safe_to_spend:63-66` and `calculate_runway:127-130` keep summing all kinds. This is deliberate; do not "fix" it.
- **Direction display stays sign-based.** In templates, `+`/`-` and red/green colouring describe the *amount* and remain sign tests. Only *type labels* (the "Income"/"Expense" badge, the value passed to an edit modal) become kind tests.
- **Kind vocabulary is `income` / `expense` / `inventory`**, lowercase, defined once in `models.py`. Nothing creates an `inventory` row in this piece.
- **`db.create_all()` never alters an existing table.** Schema changes to existing tables go in `_migrate_db` at `app.py:190`, which already follows a `PRAGMA table_info` → `ALTER TABLE` pattern and commits at the end.
- **Commit after every task.** Follow the existing commit-message voice in this repo: what changed and *why*, not a changelog line.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tests/test_finance_characterization.py` | Create. Pins today's numbers across every aggregation and both exports. The safety net. | 1 |
| `gigledger/models.py` | Modify. The kind vocabulary, `clean_kind`, `KindMixin`, and the column on two models. | 2 |
| `gigledger/app.py` | Modify. `_migrate_db` gains the ALTER + backfill; seed blocks set kind. | 3, 4 |
| `tests/test_transaction_kind.py` | Create. Seam behaviour, migration, creation sites, and the foundation test. | 2, 3, 4, 10 |
| `gigledger/finance.py` | Modify. Three aggregations convert at `_get_tx_range`. | 5 |
| `gigledger/routes/reports.py`, `routes/dashboard.py` | Modify. Display aggregations. | 6 |
| `gigledger/routes/transactions.py` | Modify. Filters, exports, labels; gains two small shared helpers. | 7 |
| `gigledger/routes/recurring.py` | Modify. Commitment sum, sign coercion, generation. | 8 |
| `gigledger/templates/transactions/index.html`, `templates/recurring/index.html` | Modify. Type badges and edit-modal kind arguments. | 9 |
| `docs/adr/0010-classify-transactions-by-kind.md`, `docs/adr/README.md` | Create/modify. The decision record. | 10 |

---

### Task 1: Characterization tests

Pins every number the app currently computes, *before* any production code changes. Nothing in this task touches `gigledger/`. If a later task breaks a number, this is what says so.

The demo seed uses `random`, so these tests build their own fixture ledger with known amounts rather than relying on seeded data.

**Files:**
- Test: `tests/test_finance_characterization.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `build_app(tmp_path, monkeypatch, **config)` and the `ledger` fixture, reused by `tests/test_transaction_kind.py` in later tasks via import.

- [ ] **Step 1: Write the characterization tests**

Create `tests/test_finance_characterization.py`:

```python
"""Characterization tests for the financial aggregations.

Written before the transaction-kind refactor and unchanged by it. Every number
here is the app's behaviour as it stood at commit f5427d2; the refactor's whole
promise is that none of them move. A failure in this file after a conversion
task means the conversion changed behaviour, not that the expectation is stale -
do not edit the expected values to make it pass.

The ledger fixture is built by hand rather than from the demo seed, which uses
`random` and so cannot pin an exact figure.
"""
import io
from datetime import datetime

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import Transaction, User, db


def build_app(tmp_path, monkeypatch, **config):
    """An app backed by a throwaway database, never the real one."""
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key',
                      WTF_CSRF_ENABLED=False, **config)
    app.login_manager.session_protection = None
    return app


# (amount, category, is_tax_deductible, day) - all in March 2026, Q1.
LEDGER = [
    (5000.00, 'Client Payment', False, 3),
    (2500.00, 'Consulting', False, 10),
    (-1200.00, 'Software', True, 5),
    (-300.00, 'Travel', True, 12),
    (-450.00, 'Meal', False, 18),
]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """An app whose only transactions are LEDGER, owned by the demo user."""
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user = User.query.filter_by(email='demo@gigledger.com').first()
        Transaction.query.delete()
        for amount, category, deductible, day in LEDGER:
            db.session.add(Transaction(
                user_id=user.id, amount=amount,
                date=datetime(2026, 3, day, 12, 0),
                category=category, description='fixture',
                is_tax_deductible=deductible, source='manual'))
        db.session.commit()
    return app


def demo_user_id(app):
    with app.app_context():
        return User.query.filter_by(email='demo@gigledger.com').first().id


def authenticated_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(demo_user_id(app))
        session['_fresh'] = True
    return client


def test_monthly_summary(ledger):
    from gigledger.finance import calculate_monthly_summary
    with ledger.app_context():
        income, expenses = calculate_monthly_summary(demo_user_id(ledger), 2026, 3)
    assert income == 7500.00
    assert expenses == 1950.00


def test_quarterly_income_and_deductions(ledger):
    from gigledger.finance import calculate_quarterly_income_deductions
    with ledger.app_context():
        income, deductions = calculate_quarterly_income_deductions(
            demo_user_id(ledger), 1, 2026)
    assert income == 7500.00
    assert deductions == 1500.00


def test_quarterly_tax(ledger):
    from gigledger.finance import calculate_quarterly_tax
    with ledger.app_context():
        income, deductions, net, tax = calculate_quarterly_tax(
            demo_user_id(ledger), 1, 2026, 0.30)
    assert (income, deductions, net) == (7500.00, 1500.00, 6000.00)
    assert tax == pytest.approx(1800.00)


def test_bank_balance_is_every_transaction(ledger):
    """Cash, not profit: the balance sums all transactions regardless of kind."""
    from gigledger.finance import calculate_safe_to_spend
    with ledger.app_context():
        balance, _, _ = calculate_safe_to_spend(demo_user_id(ledger), 0.30)
    assert balance == pytest.approx(5550.00)


def test_category_breakdown_is_expenses_only(ledger):
    from gigledger.finance import get_category_breakdown
    with ledger.app_context():
        categories, totals = get_category_breakdown(
            demo_user_id(ledger), year=2026, month=3)
    assert dict(zip(categories, totals)) == {
        'Software': 1200.00, 'Travel': 300.00, 'Meal': 450.00}


def test_csv_export_rows_and_summary(ledger):
    body = authenticated_client(ledger).get(
        '/transactions/export/csv').get_data(as_text=True)
    assert 'Client Payment' in body
    assert ',Income,' in body
    assert ',Expense,' in body
    assert 'Total Income,,,,7500.00' in body
    assert 'Total Expenses,,,,1950.00' in body
    assert 'Deductible Expenses,,,,1500.00' in body
    assert 'Net,,,,5550.00' in body


def test_csv_export_type_filter(ledger):
    client = authenticated_client(ledger)
    income_only = client.get(
        '/transactions/export/csv?type=income').get_data(as_text=True)
    assert 'Total Income,,,,7500.00' in income_only
    assert 'Total Expenses,,,,0.00' in income_only

    expense_only = client.get(
        '/transactions/export/csv?type=expense').get_data(as_text=True)
    assert 'Total Income,,,,0.00' in expense_only
    assert 'Total Expenses,,,,1950.00' in expense_only


def test_pdf_export_totals(ledger):
    body = authenticated_client(ledger).get(
        '/transactions/export/pdf').get_data(as_text=True)
    assert '7,500.00' in body
    assert '1,950.00' in body


def test_transactions_list_type_filter(ledger):
    client = authenticated_client(ledger)
    assert client.get('/transactions?type=income').status_code == 200
    assert b'2 transaction' in client.get('/transactions?type=income').data
    assert b'3 transaction' in client.get('/transactions?type=expense').data


def test_reports_year_totals(ledger):
    body = authenticated_client(ledger).get(
        '/reports?year=2026').get_data(as_text=True)
    assert '7,500.00' in body
    assert '1,950.00' in body


def test_dashboard_renders(ledger):
    assert authenticated_client(ledger).get('/').status_code == 200
```

- [ ] **Step 2: Run the tests to verify they pass against current code**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py -q`
Expected: PASS. These describe today's behaviour, so they must pass *before* any production change.

If any assertion fails here, the expected value is wrong — read the actual output and correct the expectation. Do not proceed to Task 2 with a red characterization suite.

- [ ] **Step 3: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: 99 existing tests plus the new ones, all passing.

- [ ] **Step 4: Commit**

```bash
git add tests/test_finance_characterization.py
git commit -m "Pin the financial aggregations before the kind refactor

The transaction-kind change promises that no displayed number moves. That
promise needs a witness that predates the change, so these tests describe
the current behaviour of every aggregation and both exports and are not
touched by any later task.

The ledger is built by hand rather than seeded: the demo seed uses random
amounts and cannot pin an exact figure.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The kind vocabulary and seam

**Files:**
- Modify: `gigledger/models.py:11-15` (constants), `:65-78` (Transaction), `:386-400` (RecurringTransaction)
- Test: `tests/test_transaction_kind.py` (create)

**Interfaces:**
- Produces, all importable from `gigledger.models`:
  - `INCOME = 'income'`, `EXPENSE = 'expense'`, `INVENTORY = 'inventory'`
  - `KINDS: set[str]` — all three
  - `COST_KINDS: set[str]` — `{EXPENSE}`; the kinds that reduce profit
  - `clean_kind(value, fallback=EXPENSE) -> str`
  - `Transaction.kind`, `RecurringTransaction.kind` — `String(20)`, not null
  - `.is_income`, `.is_expense`, `.is_inventory` (bool properties) and `.kind_label` (`'Income'` / `'Expense'` / `'Inventory'`) on both models

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transaction_kind.py`:

```python
"""The explicit transaction kind.

The decision is recorded in docs/adr/0010. The point worth restating here: a
sign carries one bit, so classifying by `amount > 0` supports two kinds and no
more. `kind` is stored, and the sign of `amount` says only which direction the
money moved.
"""
import pytest

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: FAIL at import — `ImportError: cannot import name 'COST_KINDS' from 'gigledger.models'`.

- [ ] **Step 3: Add the vocabulary and the seam**

In `gigledger/models.py`, after `DEFAULT_EXPENSE_CATEGORIES` (line 15):

```python
# Transaction kinds. The kind is stored, never derived from the sign of the
# amount: a sign carries one bit, which is enough for two kinds and no more.
# See docs/adr/0010.
INCOME = 'income'
EXPENSE = 'expense'
INVENTORY = 'inventory'
KINDS = {INCOME, EXPENSE, INVENTORY}

# The kinds that reduce profit. An inventory purchase is cash out but not a
# cost - the money bought an asset that is still owned - so it is absent here
# and that absence is what keeps it out of every expense total.
COST_KINDS = {EXPENSE}


def clean_kind(value, fallback=EXPENSE):
    """The kind if it is one we recognise, else the fallback.

    A Constrained Column, the same treatment `clean_rate_type` and
    `clean_color` give their columns in the route modules: the write decides,
    so a reader never has to wonder whether the column holds something the
    code has never heard of.
    """
    value = (value or '').strip().lower()
    return value if value in KINDS else fallback


class KindMixin:
    """Classification shared by Transaction and RecurringTransaction.

    Both answer the question the same way, and a recurring transaction hands
    its kind to the transactions it generates, so the two must not drift.
    """

    @property
    def is_income(self):
        return self.kind == INCOME

    @property
    def is_expense(self):
        return self.kind == EXPENSE

    @property
    def is_inventory(self):
        return self.kind == INVENTORY

    @property
    def kind_label(self):
        return (self.kind or '').title()
```

Change the `Transaction` declaration at line 65 to mix it in, and add the column beside `source`:

```python
class Transaction(KindMixin, db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    kind = db.Column(db.String(20), nullable=False, default=EXPENSE)
    category = db.Column(db.String(50))
```

Do the same for `RecurringTransaction` at line 386 — mix in `KindMixin` and add the identical `kind` column after `amount`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass, characterization included. Adding an unused column changes no behaviour.

- [ ] **Step 6: Commit**

```bash
git add gigledger/models.py tests/test_transaction_kind.py
git commit -m "Give transactions an explicit kind

A sign carries one bit. Classifying by \`amount > 0\` supports income and
expense and nothing else, so inventory - cash out that is not a cost -
cannot be said at all in the current representation.

The column is added with the full vocabulary including 'inventory', even
though nothing writes that value yet, so the kind that follows is additive
rather than a second sweep of the same twenty-five sites.

COST_KINDS is the seam that matters: it answers 'does this reduce profit'
in one place, and inventory's absence from it is the whole feature.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Migration and backfill

`db.create_all()` creates missing tables and never alters an existing one, so a database that predates Task 2 will not gain the column. `_migrate_db` at `app.py:190` already handles exactly this, including for the `transactions` table, and commits at the end.

**Files:**
- Modify: `gigledger/app.py:223-227` (the transactions block inside `_migrate_db`)
- Test: `tests/test_transaction_kind.py` (append)

**Interfaces:**
- Consumes: `INCOME`, `EXPENSE` from Task 2.
- Produces: nothing importable. The guarantee is that after `create_app()`, both tables have a populated `kind` column.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transaction_kind.py`:

```python
import sqlite3

import gigledger.app
from gigledger.app import create_app


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


def kinds_in(path, table):
    conn = sqlite3.connect(path)
    rows = dict(conn.execute(f"SELECT id, kind FROM {table} ORDER BY id"))
    conn.close()
    return rows


def test_an_existing_database_gains_the_column(tmp_path, monkeypatch):
    """create_all() never alters an existing table, so the migration must."""
    db_path = str(tmp_path / 'test.db')
    legacy_database(db_path)
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()

    assert kinds_in(db_path, 'transactions') == {
        1: INCOME, 2: EXPENSE, 3: EXPENSE}
    assert kinds_in(db_path, 'recurring_transactions') == {
        1: INCOME, 2: EXPENSE}


def test_a_zero_amount_row_backfills_as_an_expense(tmp_path, monkeypatch):
    """Row 3 above is zero. It counted as neither income nor expense before,
    because both sign tests were strict, and it lands in `expense` now. No
    displayed number moves: a zero contributes zero to an expense total."""
    db_path = str(tmp_path / 'test.db')
    legacy_database(db_path)
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()

    assert kinds_in(db_path, 'transactions')[3] == EXPENSE


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

    assert kinds_in(db_path, 'transactions')[2] == INVENTORY
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -k migration or -k database -q`
Expected: FAIL with `sqlite3.OperationalError: no such column: kind`.

- [ ] **Step 3: Extend the migration**

In `gigledger/app.py`, replace the transactions block inside `_migrate_db` (currently lines 223-227) with:

```python
    # Migrate transactions table
    cursor.execute("PRAGMA table_info(transactions)")
    tx_columns = {row[1] for row in cursor.fetchall()}
    if 'invoice_id' not in tx_columns:
        cursor.execute("ALTER TABLE transactions ADD COLUMN invoice_id INTEGER REFERENCES invoices(id)")
    if 'kind' not in tx_columns:
        # Backfill from the sign, which is what classification meant until now.
        # A zero-amount row lands in 'expense': it counted as neither before,
        # and a zero contributes zero to an expense total, so no figure moves.
        cursor.execute("ALTER TABLE transactions ADD COLUMN kind VARCHAR(20)")
        cursor.execute("UPDATE transactions SET kind = "
                       "CASE WHEN amount > 0 THEN 'income' ELSE 'expense' END")

    # Migrate recurring_transactions table - kind, for the transactions it generates
    cursor.execute("PRAGMA table_info(recurring_transactions)")
    rt_columns = {row[1] for row in cursor.fetchall()}
    if 'kind' not in rt_columns:
        cursor.execute("ALTER TABLE recurring_transactions ADD COLUMN kind VARCHAR(20)")
        cursor.execute("UPDATE recurring_transactions SET kind = "
                       "CASE WHEN amount > 0 THEN 'income' ELSE 'expense' END")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gigledger/app.py tests/test_transaction_kind.py
git commit -m "Backfill kind on databases that predate the column

db.create_all() creates missing tables and never alters an existing one, so
a database with a transactions table would come up without kind and fail on
the first query. _migrate_db already exists for exactly this and already
migrates this table, so the ALTER and backfill join it there rather than
arriving as a second mechanism.

The backfill reads the sign, which is what classification meant until now.
Zero-amount rows land in 'expense'; they counted as neither before, and a
zero contributes zero to an expense total, so nothing displayed changes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Every creation site sets a kind

Eight places write a `Transaction`; two write a `RecurringTransaction`. Each sets a kind explicitly. Classification logic is untouched in this task — only writes.

**Files:**
- Modify: `gigledger/routes/transactions.py:57-70`, `:99-101`
- Modify: `gigledger/routes/invoices.py:185-208`
- Modify: `gigledger/routes/projects.py:197-205`
- Modify: `gigledger/routes/recurring.py:76`, `:100-110`, `:219-226`
- Modify: `gigledger/app.py:388`, `:394`, `:436`, `:450`, `:464`
- Test: `tests/test_transaction_kind.py` (append)

**Interfaces:**
- Consumes: `clean_kind`, `INCOME`, `EXPENSE` from Task 2.
- Produces: the invariant that no `Transaction` or `RecurringTransaction` row is ever written without a recognised kind.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transaction_kind.py`:

```python
from gigledger.models import User, db
from tests.test_finance_characterization import (authenticated_client,
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: FAIL — seeded and route-created rows carry the column default rather than a deliberate kind, so `test_seeded_kinds_agree_with_their_amounts` and `test_adding_income_stores_the_income_kind` fail.

- [ ] **Step 3: Set the kind at every write**

`gigledger/routes/transactions.py` — import the seam and drive coercion from the kind. Replace lines 57-70 in `add()`:

```python
    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)
    if kind == EXPENSE and amount > 0: amount = -amount
    elif kind == INCOME and amount < 0: amount = abs(amount)

    date_str = request.form.get('date', '')
    try: date = datetime.strptime(date_str, '%Y-%m-%d')
    except: date = datetime.now()

    tx = Transaction(
        user_id=current_user.id, amount=amount, date=date, kind=kind,
        category=request.form.get('category', 'Uncategorized'),
        description=request.form.get('description', ''),
        is_tax_deductible=request.form.get('is_tax_deductible') == 'on',
        source='manual')
```

and lines 99-101 in `edit()`:

```python
    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)
    if kind == EXPENSE and amount > 0: amount = -amount
    elif kind == INCOME and amount < 0: amount = abs(amount)
    tx.kind = kind
```

Update its import line to `from ..models import Transaction, db, clean_kind, EXPENSE, INCOME`.

`gigledger/routes/invoices.py` — add `kind=INCOME` to the `Transaction(...)` at line 185 and `kind=EXPENSE` to the one at line 199; import `INCOME, EXPENSE` from `..models`.

`gigledger/routes/projects.py` — add `kind=INCOME` to the `Transaction(...)` at line 197; import `INCOME` from `..models`.

`gigledger/routes/recurring.py` — in `add()`, replace the coercion at line 76 with:

```python
    kind = clean_kind(tx_type, fallback=EXPENSE)
    if kind == EXPENSE and amount > 0:
        amount = -amount
```

add `kind=kind,` to the `RecurringTransaction(...)` at line 100, and add `kind=rt.kind,` to the `Transaction(...)` at line 219. Import `clean_kind, EXPENSE` from `..models`.

`gigledger/app.py` — in `_seed_demo_data`, add `kind='income'` to the invoice income transaction (line 388) and the income block (line 436); `kind='expense'` to the tax reserve (line 394) and both expense blocks (lines 450, 464). Add `kind='expense'` to the recurring seed data at line 562 if its dicts do not already carry one — check each entry's sign and set the matching kind.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass, characterization included — no classification logic has changed yet.

- [ ] **Step 6: Commit**

```bash
git add gigledger/ tests/test_transaction_kind.py
git commit -m "Set a kind at every site that writes a transaction

Ten writes across routes and the demo seed. A row written without a kind is
a row no later aggregation can classify, and the column default would hide
that behind a plausible-looking 'expense'.

The manual add and edit forms now derive the sign from the kind rather than
the reverse, so the kind is the decision and the sign follows it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Convert `finance.py`

`_get_tx_range` is a single funnel feeding three aggregations, so widening its tuple converts them at the source.

**Files:**
- Modify: `gigledger/finance.py:10` (import), `:31-39`, `:50-51`, `:88-89`, `:185`
- Test: `tests/test_finance_characterization.py` must keep passing unchanged.

**Interfaces:**
- Consumes: `COST_KINDS`, `INCOME`, `EXPENSE` from Task 2.
- Produces: `_get_tx_range(user_id, start_date, end_date) -> list[tuple[float, bool, str]]` — `(amount, is_tax_deductible, kind)`. Task 6 consumes this in `dashboard.py`.

- [ ] **Step 1: Widen the funnel**

In `gigledger/finance.py`, change the import at line 10 to:

```python
from .models import COST_KINDS, EXPENSE, INCOME, Transaction, db
```

Replace `_get_tx_range` (lines 31-39):

```python
def _get_tx_range(user_id, start_date, end_date):
    """Fetch amounts, deductible flags and kinds for a date range.

    The kind comes along because classification is this tuple's job: every
    caller needs to know what a row *is*, and the sign of the amount only
    says which way the money moved. See docs/adr/0010.
    """
    return db.session.query(
        Transaction.amount, Transaction.is_tax_deductible, Transaction.kind
    ).filter(
        Transaction.user_id == user_id,
        Transaction.date >= start_date,
        Transaction.date < end_date,
    ).all()
```

Replace lines 50-51 in `calculate_monthly_summary`:

```python
    income = sum(amount for amount, _, kind in results if kind == INCOME)
    expenses = abs(sum(amount for amount, _, kind in results if kind in COST_KINDS))
```

Replace lines 88-89 in `calculate_quarterly_income_deductions`:

```python
    income = sum(amount for amount, _, kind in results if kind == INCOME)
    deductions = sum(abs(amount) for amount, deductible, kind in results
                     if kind in COST_KINDS and deductible)
```

Replace the filter at line 185 in `get_category_breakdown`:

```python
        Transaction.kind == EXPENSE,
```

Leave the balance queries at lines 63-66 and 127-130 exactly as they are. Add a comment above the one in `calculate_safe_to_spend`:

```python
    # Every kind, deliberately. This is cash, not profit: an inventory
    # purchase is money that has left the bank. See docs/adr/0010.
```

- [ ] **Step 2: Run the characterization tests**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py -q`
Expected: PASS, with no edits to that file. This is the proof that no number moved.

- [ ] **Step 3: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add gigledger/finance.py
git commit -m "Classify by kind in the financial aggregations

_get_tx_range turns out to be a single funnel feeding the monthly summary,
the quarterly calculation and the dashboard's deductible figure, so widening
its tuple to carry the kind converts all three at the source rather than at
scattered call sites.

The two balance queries keep summing every kind, with a comment saying why:
they measure cash, and inventory cash really does leave the account. That
asymmetry is the design.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Convert `reports.py` and `dashboard.py`

**Files:**
- Modify: `gigledger/routes/reports.py:46-49`, `:102`, `:123-124`
- Modify: `gigledger/routes/dashboard.py:47`, `:69`
- Test: `tests/test_finance_characterization.py` must keep passing unchanged.

**Interfaces:**
- Consumes: `_get_tx_range`'s three-wide tuple from Task 5; `is_income` / `is_expense` from Task 2.

- [ ] **Step 1: Convert reports.py**

Import the seam: `from ..models import Transaction, EXPENSE, db` (keep whatever else the file already imports).

Replace lines 46-49:

```python
    yearly_income = sum(t.amount for t in year_txs if t.is_income)
    yearly_expenses = sum(abs(t.amount) for t in year_txs if t.is_expense)
    yearly_net = yearly_income - yearly_expenses
    yearly_deductible = sum(abs(t.amount) for t in year_txs
                            if t.is_expense and t.is_tax_deductible)
```

(Keep the existing `yearly_net` line as it stands if its wording differs — only the three classification lines change.)

Replace the filter at line 102:

```python
        Transaction.kind == EXPENSE,
```

Replace lines 123-124:

```python
        q_income = sum(t.amount for t in q_txs if t.is_income)
        q_expenses = sum(abs(t.amount) for t in q_txs if t.is_expense)
```

Leave line 77 (`m['income'] > 0`) alone — it reads a computed total, not a transaction.

- [ ] **Step 2: Convert dashboard.py**

Replace line 47:

```python
    deductible_this_month = sum(abs(amount) for amount, deductible, kind in results
                                if kind in COST_KINDS and deductible)
```

Replace line 69:

```python
    monthly_commitment = sum(abs(r.amount) for r in recurring_active if r.is_expense)
```

Add `COST_KINDS` to the `..models` import.

- [ ] **Step 3: Run the characterization tests**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py -q`
Expected: PASS with no edits.

- [ ] **Step 4: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add gigledger/routes/reports.py gigledger/routes/dashboard.py
git commit -m "Classify by kind in the reports and dashboard totals

Seven sites that asked the sign what a transaction was now ask the row. The
top-categories query filters on kind in SQL rather than on a negative
amount, so an inventory purchase will not appear among expense categories
once such rows exist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Convert `transactions.py`

The same filter block appears three times (list, CSV export, PDF export) and the same three totals twice. Converting each copy separately would leave three places for a fourth kind to be forgotten, so this task extracts both into helpers as it converts them.

**Files:**
- Modify: `gigledger/routes/transactions.py` — imports, `:9-46`, `:130-199`, `:202-257`
- Test: `tests/test_finance_characterization.py` must keep passing unchanged.

**Interfaces:**
- Consumes: `KINDS`, `is_income` / `is_expense` / `kind_label` from Task 2.
- Produces, module-private:
  - `_filtered_transactions(uid, args) -> list[Transaction]`
  - `_totals(transactions, tax_rate) -> tuple[float, float, float, float, float]` — `(income, expenses, deductible, net, tax_saving)`

- [ ] **Step 1: Add the shared helpers**

In `gigledger/routes/transactions.py`, update the import to
`from ..models import Transaction, db, clean_kind, EXPENSE, INCOME, KINDS`
and add, above `list_transactions`:

```python
def _filtered_transactions(uid, args):
    """The transaction list the index and both exports share.

    Extracted while converting to kind because the filter existed in three
    copies. A fourth kind should be a change in one place, not three.
    """
    transactions = Transaction.query.filter_by(user_id=uid)\
        .order_by(Transaction.date.desc()).all()

    category = args.get('category', '')
    if category:
        transactions = [t for t in transactions if t.category == category]

    kind = args.get('type', '')
    if kind in KINDS:
        transactions = [t for t in transactions if t.kind == kind]

    month = args.get('month', '')
    if month:
        try:
            m = int(month)
            transactions = [t for t in transactions if t.date.month == m]
        except ValueError:
            pass

    year = args.get('year', '')
    if year:
        try:
            y = int(year)
            transactions = [t for t in transactions if t.date.year == y]
        except ValueError:
            pass

    return transactions


def _totals(transactions, tax_rate):
    """(income, expenses, deductible, net, tax_saving) for a filtered list."""
    income = sum(t.amount for t in transactions if t.is_income)
    expenses = sum(abs(t.amount) for t in transactions if t.is_expense)
    deductible = sum(abs(t.amount) for t in transactions
                     if t.is_expense and t.is_tax_deductible)
    return income, expenses, deductible, income - expenses, deductible * tax_rate
```

- [ ] **Step 2: Use them in all three views**

In `list_transactions`, replace the fetch-and-filter block (lines 18-36) with:

```python
    transactions = _filtered_transactions(uid, request.args)
```

keeping the existing `categories` lines and the `render_template` call as they are.

In `export_csv`, replace the filter block (lines 142-160) with the same one-liner, and replace the totals at lines 179-181 with:

```python
    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, current_user.default_tax_rate)
```

deleting the now-duplicated `net` and `tax_saving` lines beneath them.

Replace the type cell at line 171:

```python
            tx.kind_label,
```

In `export_pdf`, replace its filter block (lines 214-231) and its totals (lines 236-240) with the same two calls.

- [ ] **Step 3: Run the characterization tests**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py -q`
Expected: PASS with no edits. `test_csv_export_type_filter` and `test_transactions_list_type_filter` are the ones that matter here.

- [ ] **Step 4: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass, including `tests/test_no_handbuilt_html.py`, which lints this file.

- [ ] **Step 5: Commit**

```bash
git add gigledger/routes/transactions.py
git commit -m "Classify by kind in the transaction list and exports

The filter block existed in three copies and the totals in two, so
converting each in place would have left three places for a fourth kind to
be forgotten. They are one helper each now, extracted while converting
rather than as a separate tidy-up.

The CSV type column reads kind_label instead of testing the sign, so it will
say Inventory when there is inventory to say it about.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Convert `recurring.py`

**Files:**
- Modify: `gigledger/routes/recurring.py:44`, `:126-135`
- Test: `tests/test_transaction_kind.py` (append)

**Interfaces:**
- Consumes: `is_expense`, `clean_kind` from Task 2. `add()` and `generate()` were already converted in Task 4.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transaction_kind.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -k recurring_amount -q`
Expected: FAIL — the edit path currently re-derives direction from `rt.amount > 0`, which is the pattern being removed.

- [ ] **Step 3: Convert the two remaining sites**

Replace line 44:

```python
    monthly_commitments = sum(abs(r.amount) for r in recurring
                              if r.is_active and r.is_expense and r.frequency == 'monthly')
```

Replace the amount coercion in `edit()` (lines 129-133):

```python
    try:
        amount = float(request.form.get('amount', str(abs(rt.amount))))
        if amount > 0:
            rt.amount = -amount if rt.is_expense else amount
    except ValueError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gigledger/routes/recurring.py tests/test_transaction_kind.py
git commit -m "Classify recurring transactions by kind

The edit path re-derived direction from the stored sign, so the sign was
both the input and the output of the same decision. It reads the kind now,
which is the only version that survives a third kind.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Convert the type labels in templates

Templates classify by sign in two distinct ways, and only one of them is wrong. `+`/`-` and red/green describe the **amount** and stay sign tests. The type badge and the kind handed to an edit modal describe **what the row is** and become kind tests.

**Files:**
- Modify: `gigledger/templates/transactions/index.html:131`, `:152`, `:161`
- Modify: `gigledger/templates/recurring/index.html:72`, `:112`
- Test: `tests/test_transaction_kind.py` (append)

**Interfaces:**
- Consumes: `.kind` and `.kind_label` from Task 2.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transaction_kind.py`:

```python
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

    assert '"inventory"' in body
    assert 'Inventory' in body
```

Add `from datetime import datetime` to the test file's imports if it is not already there.

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -k edit_modal -q`
Expected: FAIL — the template renders `'income' if tx.amount > 0 else 'expense'`, so a negative inventory row is labelled `expense`.

- [ ] **Step 3: Convert the type labels**

In `gigledger/templates/transactions/index.html`, line 131, replace the badge's content with `{{ tx.kind_label }}` and key its colour off `tx.is_income`:

```jinja
                            <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold {% if tx.is_income %}bg-green-50 text-green-600{% else %}bg-gray-100/80 text-gray-600{% endif %}">
```

At line 152, replace `{% elif tx.amount > 0 %}` with `{% elif tx.is_income %}`.

At line 161, replace the third argument to `openEditModal`:

```jinja
{{ tx.kind|tojson|forceescape }}
```

In `gigledger/templates/recurring/index.html`, apply the same three changes at lines 72 and 112 (`rt.is_income`, `rt.kind_label`, `rt.kind|tojson|forceescape`).

Leave lines 127, 135-136, 139, 149 in the transactions template and 60, 68-69 in the recurring one exactly as they are: those are direction and deductibility, not classification.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass, including `tests/test_template_escaping.py` — the modal argument keeps its `|tojson|forceescape` per ADR-0004.

- [ ] **Step 6: Commit**

```bash
git add gigledger/templates/
git commit -m "Label transaction type from the kind, not the sign

Templates read the sign for two different purposes and only one of them was
wrong. The + and the red-green stay: they describe the amount. The type
badge and the kind handed to the edit modal describe what the row is, and a
negative inventory row would have been labelled an expense in both.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: The foundation test and ADR-0010

The test that proves the piece did its job. Nothing in the UI can create an inventory row yet, which is exactly why this has to be written by hand: it is the evidence that piece 2 has a foundation, and it fails loudly if a later change lets inventory leak into a cost total.

**Files:**
- Test: `tests/test_transaction_kind.py` (append)
- Create: `docs/adr/0010-classify-transactions-by-kind.md`
- Modify: `docs/adr/README.md` (index row)

**Interfaces:**
- Consumes: everything from Tasks 2-9.

- [ ] **Step 1: Write the foundation test**

Append to `tests/test_transaction_kind.py`:

```python
INVENTORY_PURCHASE = 980.00


@pytest.fixture
def with_inventory(tmp_path, monkeypatch):
    """A ledger plus one inventory purchase, built by hand because no UI can
    make one yet. That is the point: this fixture is piece 2's foundation
    under test before piece 2 exists."""
    from tests.test_finance_characterization import LEDGER
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
    assert b'Sectional sofa' in client.get('/transactions').data
    csv_body = client.get('/transactions/export/csv').get_data(as_text=True)
    assert ',Inventory,' in csv_body
    assert 'Total Expenses,,,,1950.00' in csv_body
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_transaction_kind.py -q`
Expected: PASS. If any fail, a conversion site was missed — find it before writing the ADR.

- [ ] **Step 3: Write ADR-0010**

Create `docs/adr/0010-classify-transactions-by-kind.md` following the format in `docs/adr/README.md`:

```markdown
# 0010. Classify transactions by an explicit kind, not the sign of the amount

- **Status:** Accepted
- **Date:** 2026-08-29

## Context

`Transaction` had no column saying what kind of transaction it was. The kind was
inferred from the sign of `amount` - `'Income' if tx.amount > 0 else 'Expense'` -
in roughly twenty-five places across the aggregations, the reports, both exports
and the templates.

A sign carries one bit. It expresses two kinds and no more, so a third kind was
not something the representation could hold. Inventory is that third kind, and
it behaves like neither existing one: an inventory purchase is cash leaving the
bank that is *not* an expense, because the money bought an asset that is still
owned. Every expense total in the app would have absorbed it silently.

## Decision

We store the kind: `income`, `expense`, or `inventory`, on both `Transaction`
and `RecurringTransaction`. Amounts stay signed, so the sign says which way the
money moved and the kind says what the movement was.

One seam in `models.py` owns the vocabulary. `COST_KINDS` answers "does this
reduce profit" in a single place, and inventory's absence from that set is the
entire behaviour. `clean_kind` constrains the column at the write, as
`clean_rate_type` and `clean_color` do for theirs.

Existing databases are backfilled from the sign by `_migrate_db`, which already
handles this table. `db.create_all()` creates missing tables and never alters an
existing one, so the column would otherwise never appear.

## Consequences

A fourth kind is now a change to one set and the sites that name it, rather than
a survey of every comparison against zero.

**The two balance sums stay kind-blind on purpose.** `calculate_safe_to_spend`
and `calculate_runway` sum every kind, because they measure cash and inventory
cash really does leave the account. This looks like a missed conversion and is
not one. Do not "fix" it.

Templates read the sign for two purposes and only one was classification. The
`+` and the red-green colouring describe the amount and remain sign tests; the
type badge and the edit-modal argument read the kind.

Backfilling by sign puts zero-amount rows in `expense`. They counted as neither
before, because both sign tests were strict. No displayed figure moves, since a
zero contributes zero to an expense total.

This does not protect against a kind written outside `clean_kind` - a direct
`db.session.add` with a typo'd string still lands in the column. The seeded and
route-level writes are covered by tests; a future writer is not.
```

- [ ] **Step 4: Add the index row**

In `docs/adr/README.md`, append to the index table:

```markdown
| [0010](0010-classify-transactions-by-kind.md) | Classify transactions by an explicit kind, not the sign of the amount | Accepted |
```

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/python -m pytest -q`
Expected: all pass — the 99 originals, the characterization suite, and the kind suite.

- [ ] **Step 6: Commit**

```bash
git add tests/test_transaction_kind.py docs/adr/
git commit -m "Prove an inventory row stays out of every cost total

Nothing in the UI can create an inventory transaction yet, so this fixture
builds one by hand. That is the point: the test is the evidence that piece 2
has a foundation, and it fails loudly if a later change lets inventory leak
into an expense, a deduction or a category breakdown.

It also pins the asymmetry that makes the feature work - the purchase is
absent from every cost total and present in the bank balance, because the
money left the account even though the cost is not recognised.

ADR-0010 records the one-bit argument and names the two balance sums that
stay sign-blind, so the next reader does not tidy them into consistency.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: vocabulary and seam → 2; migration and zero-amount rows → 3; the conversion checklist → 5, 6, 7, 8; creation sites → 4; "deliberately unchanged" → 5 (comment) and 10 (ADR); testing → 1, 3, 4, 10; ADR → 10.

**One gap found and closed.** The spec's checklist omitted templates entirely, but `transactions/index.html` and `recurring/index.html` classify by sign in five places, including the kind argument handed to each edit modal. Task 9 covers them, and the spec should be amended to match.

**One spec correction.** The spec described the startup migration as new. `_migrate_db` already exists at `app.py:190` and already migrates the `transactions` table; Task 3 extends it rather than adding a mechanism.

**Type consistency.** `_get_tx_range` returns `(amount, is_tax_deductible, kind)` in Task 5 and is unpacked that way in Task 6. `_filtered_transactions(uid, args)` and `_totals(transactions, tax_rate)` are defined and called with matching signatures within Task 7. `clean_kind(value, fallback=EXPENSE)` is defined in Task 2 and called with that signature in Tasks 4 and 8. `kind_label` is defined in Task 2 and used in Tasks 7 and 9.
