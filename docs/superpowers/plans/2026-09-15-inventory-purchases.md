# Inventory Purchases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An inventory purchase can be recorded, edited, listed and filtered — as one ledger Transaction plus one linked InventoryItem carrying quantity, unit cost, consumable/reusable and a project or General Inventory.

**Architecture:** `Transaction` stays the ledger line (`kind='inventory'`, negative amount). A new `inventory_items` table, 1:1 with its purchase, holds the asset facts. All writes go through `transactions.py`; a new read-only `/inventory` page and a section on project detail are views over the ledger. One sign rule for every kind (`abs` for income, `-abs` otherwise); a kind change into or out of inventory is refused. Monthly commitment becomes a cash figure (`not is_income`).

**Tech Stack:** Flask 3, Flask-SQLAlchemy, SQLite, Jinja2, Tailwind (CDN classes), pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-inventory-purchases-design.md`. Read it first; every task below argues from it. Also read `docs/adr/0010-classify-transactions-by-kind.md`.

## Global Constraints

- Run tests with `source venv/bin/activate && python -m pytest -q` from the repo root. Baseline before this plan: 153 passed.
- `tests/test_finance_characterization.py` must stay green and unedited: income and expense behaviour does not move.
- `tests/test_template_escaping.py` lints every `{{ }}` inside an `on*=` attribute: each must carry `|tojson|forceescape`. Every new `onclick` argument in templates follows this.
- `tests/test_no_handbuilt_html.py` forbids HTML string literals in `gigledger/routes/*.py`. All markup goes in templates.
- `tests/test_csrf.py` walks the url_map: every new POST route is covered automatically; every POST form in a template needs `{{ csrf_field() }}`.
- Templates build URLs with `url_port('blueprint.endpoint', ...)`, never `url_for`.
- Form field names are fixed by the spec and shared by every task: `type`, `amount`, `date`, `category`, `description`, `is_tax_deductible`, `quantity`, `unit_cost`, `is_consumable`, `project_id` (empty string = General Inventory).
- The inventory kind is styled amber (`amber-400`/`amber-50`/`amber-700`), matching the Monthly Commitments card.
- Commit messages: imperative subject, body explains why, end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Do not touch `gigledger.db` in the repo root. Tests use `tmp_path` databases via `build_app`.

## File Structure

| File | Responsibility |
|---|---|
| `gigledger/models.py` | `InventoryItem`; `Transaction.inventory_item`; `DEFAULT_INVENTORY_CATEGORIES`, `INVENTORY_CATEGORY_GUIDANCE`; `User.custom_inventory_categories`, `get_inventory_categories()`, kind-aware `get_all_categories()` |
| `gigledger/app.py` | migration for the new `users` column; register `inventory_bp`; expose `inventory_guidance` to templates |
| `gigledger/routes/transactions.py` | the only writer of inventory rows: sign rule, `_inventory_fields`, add/edit/delete |
| `gigledger/routes/inventory.py` (new) | read-only `/inventory` |
| `gigledger/routes/dashboard.py`, `routes/recurring.py` | monthly commitment as cash |
| `gigledger/routes/projects.py` | project detail passes its items |
| `gigledger/routes/settings.py` | third category block, shared add/delete helpers |
| `gigledger/templates/transactions/_edit_modal.html` (new) | the edit modal + shared kind/category JS, included by two pages |
| `gigledger/templates/transactions/index.html` | third radio, inventory block, filter option, include the partial |
| `gigledger/templates/transactions/export.html` | Type column |
| `gigledger/templates/inventory/index.html` (new) | the pool page |
| `gigledger/templates/projects/detail.html` | Inventory section |
| `gigledger/templates/settings/index.html` | Inventory Categories block |
| `gigledger/templates/base.html` | nav entry |
| `tests/test_inventory.py` (new) | every test in this plan |
| `docs/adr/0011-inventory-purchases-as-ledger-lines.md` (new), `docs/adr/0010-…`, `docs/adr/README.md`, `docs/glossary.md`, `README.md` | records |

Test helpers: import `build_app`, `authenticated_client`, `demo_user_id` from `test_finance_characterization` (tests run with `tests/` on `sys.path`, as `test_transaction_kind.py` does). The demo user is `demo@gigledger.com`; `_seed_demo_data()` gives it three projects.

---

### Task 1: The model, the categories, the migration

**Files:**
- Modify: `gigledger/models.py` (constants at top, `User` at ~line 76, `Transaction` at ~line 125, new class after `Transaction`)
- Modify: `gigledger/app.py:_migrate_db` (~line 200, the users block)
- Create: `tests/test_inventory.py`

**Interfaces:**
- Produces: `InventoryItem(user_id, transaction_id, project_id, quantity, unit_cost, is_consumable)` with `.total_cost`, `.location_label`, `.usage_label`, `.form_values`, `.transaction`, `.project`; `Transaction.inventory_item` (one or None); `Project.inventory_items`; `DEFAULT_INVENTORY_CATEGORIES` (list of 7), `INVENTORY_CATEGORY_GUIDANCE` (dict); `User.custom_inventory_categories`, `User.get_inventory_categories()`, `User.get_all_categories(kinds=KINDS)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inventory.py`:

```python
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
    db_path = str(tmp_path / 'test.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, "
                 "email VARCHAR(120), password_hash VARCHAR(128))")
    conn.commit()
    conn.close()
    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)

    create_app()
    create_app()  # a second startup must be a no-op

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    conn.close()
    assert 'custom_inventory_categories' in columns
```

- [ ] **Step 2: Run to verify they fail**

Run: `source venv/bin/activate && python -m pytest tests/test_inventory.py -q`
Expected: ImportError on `DEFAULT_INVENTORY_CATEGORIES` / `InventoryItem`.

- [ ] **Step 3: Add the constants**

In `gigledger/models.py`, directly after `DEFAULT_EXPENSE_CATEGORIES`:

```python
DEFAULT_INVENTORY_CATEGORIES = ['Casegoods & Storage', 'Seating', 'Lighting',
                                'Soft Goods & Textiles', 'Wall Decor & Art',
                                'Tabletop & Decorative Accessories', 'Outdoor & Patio']

# Shown under the category picker so people file consistently. Guidance, not
# data: nothing is stored from this dict, and a custom category has none.
INVENTORY_CATEGORY_GUIDANCE = {
    'Casegoods & Storage': 'Beds, nightstands, dressers, chests, armoires; TV stands, media consoles, bookcases, shelving, sideboards, credenzas; dining tables, buffets, desks, filing cabinets',
    'Seating': 'Sofas, sectionals, loveseats, accent chairs, recliners, ottomans, benches; upholstered dining chairs, executive desk chairs',
    'Lighting': 'Chandeliers, pendants, flush mounts, track lighting; table, floor and desk lamps; wall sconces, vanity lights, picture lights',
    'Soft Goods & Textiles': 'Curtains, drapes, blinds, shades and hardware; area rugs, runners, doormats, rug pads; sheets, comforters, duvet covers, pillows, bath towels, shower curtains; throw pillows, poufs, blankets',
    'Wall Decor & Art': 'Framed canvas prints, paintings, photographic prints, wall sculptures; mirrors, wall clocks, floating shelves',
    'Tabletop & Decorative Accessories': 'Vases, sculptures, decorative bowls, trays, candles and holders, picture frames; faux plants, dried florals, planters, pots; dinnerware, glassware, flatware, serveware, table linens',
    'Outdoor & Patio': 'Outdoor seating, dining sets, fire pits, outdoor rugs, weather-resistant lighting',
}
```

- [ ] **Step 4: Extend `User`**

Add the column after `custom_expense_categories`:

```python
    custom_inventory_categories = db.Column(db.Text, default='')  # comma-separated
```

Replace `get_all_categories` and add the getter:

```python
    def get_inventory_categories(self):
        if self.custom_inventory_categories:
            return [c.strip() for c in self.custom_inventory_categories.split(',') if c.strip()]
        return DEFAULT_INVENTORY_CATEGORIES.copy()

    def get_all_categories(self, kinds=KINDS):
        """The category lists for `kinds`, merged in kind order, duplicates
        dropped. Kind-aware so a page that cannot create an inventory row
        does not offer seven categories it cannot use."""
        merged = []
        if INCOME in kinds:
            merged += self.get_income_categories()
        if EXPENSE in kinds:
            merged += self.get_expense_categories()
        if INVENTORY in kinds:
            merged += self.get_inventory_categories()
        return list(dict.fromkeys(merged))
```

- [ ] **Step 5: Add the relationship on `Transaction` and the new class**

In `Transaction`, after `created_at`:

```python
    # The asset half of an inventory purchase; None for every other kind.
    # delete-orphan: the item has no meaning without its purchase.
    inventory_item = db.relationship('InventoryItem', back_populates='transaction',
                                     uselist=False, cascade='all, delete-orphan')
```

After the `Transaction` class:

```python
class InventoryItem(db.Model):
    """The asset half of an inventory purchase.

    One row per purchase Transaction, never shared: `amount` on the transaction
    is always -(quantity * unit_cost), so the ledger and the pool cannot
    disagree. A separate table rather than nullable columns on Transaction
    because an item has a lifecycle a ledger line does not - it is placed,
    consumed, returned (piece 3) - and the ADR-0006 argument for one table
    ("every other operation is identical") does not hold. See docs/adr/0011.

    `project_id` NULL means General Inventory: bought for stock, not for a job.
    """
    __tablename__ = 'inventory_items'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    transaction_id = db.Column(db.Integer, db.ForeignKey('transactions.id'),
                               nullable=False, unique=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=True)
    quantity = db.Column(db.Float, nullable=False)   # yards of fabric, not only chairs
    unit_cost = db.Column(db.Float, nullable=False)
    is_consumable = db.Column(db.Boolean, nullable=False, default=False)  # False = reusable
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # No delete cascade from Project on purpose: deleting a project returns
    # its items to General Inventory (SQLAlchemy nulls the FK), it does not
    # destroy assets that are still owned.
    project = db.relationship('Project', backref='inventory_items', lazy=True)
    transaction = db.relationship('Transaction', back_populates='inventory_item')

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost

    @property
    def location_label(self):
        return self.project.name if self.project else 'General Inventory'

    @property
    def usage_label(self):
        return 'Consumable' if self.is_consumable else 'Reusable'

    @property
    def form_values(self):
        """What the edit modal needs to prefill its inventory block."""
        return {'quantity': self.quantity, 'unit_cost': self.unit_cost,
                'is_consumable': self.is_consumable, 'project_id': self.project_id}
```

- [ ] **Step 6: Migrate the users column**

In `gigledger/app.py:_migrate_db`, after the `custom_expense_categories` line:

```python
    if 'custom_inventory_categories' not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_inventory_categories TEXT DEFAULT ''")
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_inventory.py -q`
Expected: 9 passed.

Run: `python -m pytest -q`
Expected: 162 passed (153 + 9).

- [ ] **Step 8: Commit**

```bash
git add gigledger/models.py gigledger/app.py tests/test_inventory.py
git commit -m "Give an inventory purchase an asset row

InventoryItem is 1:1 with its purchase Transaction: quantity, unit cost,
consumable flag, and a project or NULL for General Inventory. A separate
table because an item has a lifecycle a ledger line does not. The seven
inventory categories arrive with their guidance text, and
get_all_categories() becomes kind-aware so they only appear where an
inventory row can exist.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: One sign rule, and `add()` writes an inventory purchase

**Files:**
- Modify: `gigledger/routes/transactions.py` (imports line 4; `add()` lines 81-116)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `InventoryItem`, `Transaction.inventory_item`, `Project` from Task 1.
- Produces: `_signed(amount, kind)`, `InvalidInventory(ValueError)`, `_inventory_fields(form, uid) -> (quantity, unit_cost, is_consumable, project_id)` — Task 3 reuses all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k "add or sign or refused or deductible or project"`
Expected: the inventory tests fail (no item written / amount is `-0.0` or `0`); the sign-rule tests pass already (existing coercion agrees for these cases).

- [ ] **Step 3: The helpers and the new `add()`**

Replace the import line and add helpers below `_totals` in `gigledger/routes/transactions.py`:

```python
from ..models import (Transaction, InventoryItem, Project, db, clean_kind,
                      EXPENSE, INCOME, INVENTORY, KINDS)
```

```python
def _signed(amount, kind):
    """Income is cash in; every other kind is cash out.

    The same rule recurring.py applies, so a row's sign cannot depend on which
    file wrote it. A kind outside the vocabulary - NULL on a migrated
    database, see ADR-0010 - leaves the sign alone rather than inventing one.
    """
    if kind not in KINDS:
        return amount
    return abs(amount) if kind == INCOME else -abs(amount)


class InvalidInventory(ValueError):
    """An inventory form the route will not write. str() is the flash text."""


def _inventory_fields(form, uid):
    """(quantity, unit_cost, is_consumable, project_id) from the form.

    Raises InvalidInventory rather than writing something that does not add
    up: the amount is derived from these two numbers, so a zero or a blank
    here is a zero-amount ledger line with an asset row behind it.
    """
    try:
        quantity = float(form.get('quantity', ''))
        unit_cost = float(form.get('unit_cost', ''))
    except ValueError:
        raise InvalidInventory(
            'Quantity and unit cost are required for an inventory purchase.')
    if quantity <= 0 or unit_cost <= 0:
        raise InvalidInventory('Quantity and unit cost must be greater than zero.')

    project_id = None
    raw = (form.get('project_id') or '').strip()
    if raw:
        try:
            candidate = int(raw)
        except ValueError:
            raise InvalidInventory('Choose a project or General Inventory.')
        if not Project.query.filter_by(id=candidate, user_id=uid).first():
            raise InvalidInventory('Choose a project or General Inventory.')
        project_id = candidate

    return quantity, unit_cost, form.get('is_consumable') == 'on', project_id
```

Replace the body of `add()` from `try: amount = ...` through `db.session.commit()`:

```python
    back = request.referrer or url_for('transactions.list_transactions')
    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)

    item_fields = None
    if kind == INVENTORY:
        try:
            item_fields = _inventory_fields(request.form, current_user.id)
        except InvalidInventory as why:
            flash(str(why), 'error')
            return redirect(back)
        quantity, unit_cost, _, _ = item_fields
        amount = quantity * unit_cost
    else:
        try:
            amount = float(request.form.get('amount', '0'))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(back)
    amount = _signed(amount, kind)

    date_str = request.form.get('date', '')
    try: date = datetime.strptime(date_str, '%Y-%m-%d')
    except: date = datetime.now()

    tx = Transaction(
        user_id=current_user.id, amount=amount, date=date, kind=kind,
        category=request.form.get('category', 'Uncategorized'),
        description=request.form.get('description', ''),
        # Inventory is an asset, not a cost, so it is never deductible
        # whatever a stray checkbox posts.
        is_tax_deductible=(kind != INVENTORY
                           and request.form.get('is_tax_deductible') == 'on'),
        source='manual')
    if item_fields:
        quantity, unit_cost, is_consumable, project_id = item_fields
        tx.inventory_item = InventoryItem(
            user_id=current_user.id, project_id=project_id,
            quantity=quantity, unit_cost=unit_cost, is_consumable=is_consumable)
    db.session.add(tx)
    db.session.commit()
```

Keep the flash block and the final `return redirect(request.referrer or url_for('dashboard.index'))` that follow.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_inventory.py tests/test_transaction_kind.py tests/test_finance_characterization.py -q`
Expected: all pass. (`test_an_unrecognised_type_does_not_reach_the_column` still passes: `clean_kind` falls back to INCOME before `_signed` runs.)

- [ ] **Step 5: Commit**

```bash
git add gigledger/routes/transactions.py tests/test_inventory.py
git commit -m "Let add() record an inventory purchase

One sign rule for every kind - income is cash in, everything else is
cash out - replacing the two-branch coercion that disagreed with
recurring.py. For inventory the posted amount is ignored: the amount is
-(quantity x unit cost), validated positive, with the project checked
against the current user's own. Inventory is never deductible.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `edit()` — the blocker fix

**Files:**
- Modify: `gigledger/routes/transactions.py:edit()` (~lines 119-149)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `_signed`, `_inventory_fields`, `InvalidInventory` from Task 2.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k "edit or delet"`
Expected: `test_editing_only_the_date…` fails (row becomes expense); the two refusal tests fail (no flash, row changed); the update test fails (amount not recomputed).

- [ ] **Step 3: Rewrite `edit()`**

Replace everything in `edit()` after the `if not tx:` block:

```python
    back = request.referrer or url_for('transactions.list_transactions')
    kind = clean_kind(request.form.get('type', tx.kind), fallback=tx.kind)

    # A purchase cannot become an expense, or an expense a purchase, by
    # editing. The item would have to be created or orphaned mid-edit, and
    # piece 3 needs a placed item never to quietly become a cost. One wall.
    if (kind == INVENTORY) != tx.is_inventory:
        flash('Delete and re-add to change an inventory purchase into an '
              'expense, or an expense into an inventory purchase.', 'error')
        return redirect(back)

    # Validate everything before writing anything, so a refused edit is a
    # no-op rather than a half-applied one.
    item_fields = None
    if kind == INVENTORY:
        try:
            item_fields = _inventory_fields(request.form, current_user.id)
        except InvalidInventory as why:
            flash(str(why), 'error')
            return redirect(back)
        quantity, unit_cost, _, _ = item_fields
        amount = quantity * unit_cost
    else:
        try:
            amount = float(request.form.get('amount', '0'))
        except ValueError:
            flash('Invalid amount.', 'error')
            return redirect(back)

    tx.kind = kind
    tx.amount = _signed(amount, kind)

    date_str = request.form.get('date', '')
    try: tx.date = datetime.strptime(date_str, '%Y-%m-%d')
    except: pass

    tx.category = request.form.get('category', 'Uncategorized')
    tx.description = request.form.get('description', '')
    tx.is_tax_deductible = (kind != INVENTORY
                            and request.form.get('is_tax_deductible') == 'on')

    if item_fields:
        item = tx.inventory_item
        (item.quantity, item.unit_cost,
         item.is_consumable, item.project_id) = item_fields

    db.session.commit()
    flash('Transaction updated!', 'success')
    return redirect(back)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_inventory.py tests/test_transaction_kind.py tests/test_finance_characterization.py -q`
Expected: all pass, including piece 1's `test_editing_with_a_missing_type_keeps_the_stored_kind`, `test_editing_an_amount_takes_its_direction_from_the_kind_not_the_stored_sign`.

- [ ] **Step 5: Commit**

```bash
git add gigledger/routes/transactions.py tests/test_inventory.py
git commit -m "Make edit() safe for an inventory purchase

Editing an inventory row - even only its date - used to be able to post
type=expense and move it permanently into every cost total. Now a kind
change across the inventory boundary is refused before anything is
written, an inventory edit updates the item and recomputes the amount,
and every field is validated before the first write so a refused edit
changes nothing.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Monthly commitment is a cash figure

**Files:**
- Modify: `gigledger/routes/dashboard.py:70`
- Modify: `gigledger/routes/recurring.py:44-45`
- Modify: `docs/adr/0010-classify-transactions-by-kind.md:39-42`
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
# --- Monthly commitment ------------------------------------------------------

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
```

The dashboard index is `dashboard_bp.route('/')` with no prefix, so `/` is right.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_inventory.py -q -k commitment`
Expected: FAIL — both pages show `$30.00`.

- [ ] **Step 3: Change the two filters**

`gigledger/routes/dashboard.py:67-70`:

```python
    # Recurring monthly commitment: everything that leaves the account each
    # month. Cash, not cost - a recurring inventory order counts. ADR-0010.
    from ..models import RecurringTransaction
    recurring_active = RecurringTransaction.query.filter_by(user_id=uid, is_active=True, frequency='monthly').all()
    monthly_commitment = sum(abs(r.amount) for r in recurring_active if not r.is_income)
```

`gigledger/routes/recurring.py:43-45`:

```python
    # Monthly commitments: everything that leaves the account each month.
    # Cash, not cost - a recurring inventory order counts. ADR-0010.
    monthly_commitments = sum(abs(r.amount) for r in recurring
                              if r.is_active and not r.is_income and r.frequency == 'monthly')
```

- [ ] **Step 4: Record it in ADR-0010**

In `docs/adr/0010-classify-transactions-by-kind.md`, replace the paragraph beginning `**The two balance sums stay kind-blind on purpose.**` with:

```markdown
**Three cash figures stay kind-blind on purpose.** `calculate_safe_to_spend`
and `calculate_runway` sum every kind, because they measure cash and inventory
cash really does leave the account. The "Monthly Commitments" figure on the
dashboard and the recurring page is the third: it filters on `not is_income`,
so a recurring inventory order counts. A commitment is an obligation to pay,
not a P&L category. Piece 1 left this undecided and it defaulted to cost;
piece 2 decided it (2026-09-15). None of these looks like a missed conversion
and none of them is one. Do not "fix" them.
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add gigledger/routes/dashboard.py gigledger/routes/recurring.py docs/adr/0010-classify-transactions-by-kind.md tests/test_inventory.py
git commit -m "Decide monthly commitment as a cash figure

Piece 1 left it filtering on is_expense by default. A commitment is an
obligation to pay, so a recurring inventory order belongs in it: both
sums now filter on not-income, and ADR-0010 lists it as the third figure
that is kind-blind on purpose.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The transactions page — third radio, inventory block, shared edit modal, filter, export

**Files:**
- Create: `gigledger/templates/transactions/_edit_modal.html`
- Modify: `gigledger/templates/transactions/index.html` (filter lines 41-45; row lines 126-130 and 157; add modal lines 199-249; edit modal lines 252-311 → removed; script block 314-440)
- Modify: `gigledger/templates/transactions/export.html` (table head/body ~line 55-63)
- Modify: `gigledger/routes/transactions.py:list_transactions` (pass `projects`)
- Modify: `gigledger/app.py:inject_port` (expose `inventory_guidance`)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Produces: the partial `transactions/_edit_modal.html`, which expects `projects` (list of `Project`) and `currency` in context and defines the global JS `kindCategories`, `inventoryGuidance`, `fillCategories(select, kind, selectedCat)`, `applyKind(prefix, kind, selectedCat)`, `openEditModal(id, amount, type, date, category, description, deductible, item)`, `updateEditCategories()`. Element id convention: `<prefix>_amount_section`, `<prefix>_amount`, `<prefix>_inventory_section`, `<prefix>_quantity`, `<prefix>_unit_cost`, `<prefix>_consumable`, `<prefix>_project`, `<prefix>_deductible_section`, `<prefix>_ded`, `<prefix>_category`, `<prefix>_category_guidance`, with prefix `modal` (add) or `edit`.
- Task 6 includes the same partial on the Inventory page.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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
    # tojson escapes & as \u0026) both carry the inventory categories.
    assert 'Casegoods &amp; Storage' in page
    assert 'Casegoods \\u0026 Storage' in page


def test_the_edit_button_carries_the_item_for_an_inventory_row(app):
    purchase(app, quantity=2, unit_cost=490.0)
    page = authenticated_client(app).get('/transactions').get_data(as_text=True)
    # tojson|forceescape turns the quotes into &#34;
    assert '&#34;quantity&#34;: 2' in page
    assert '&#34;unit_cost&#34;: 490' in page


def test_the_html_export_shows_the_type_column(app):
    purchase(app)
    body = authenticated_client(app).get(
        '/transactions/export/pdf').get_data(as_text=True)
    assert '<th>Type</th>' in body
    assert '<td>Inventory</td>' in body
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k "filter or modal or edit_button or export"`
Expected: 4 failures.

- [ ] **Step 3: Expose the guidance and the projects**

`gigledger/app.py`, in `inject_port`, extend the returned dict:

```python
        from .models import INVENTORY_CATEGORY_GUIDANCE
        return {'xport': port, 'url_port': url_port, 'currency_symbols': CURRENCY_SYMBOLS,
                'inventory_guidance': INVENTORY_CATEGORY_GUIDANCE}
```

(Put the import at the top of the function body if `from .models import …` is already there at module level; otherwise the local import is fine.)

`gigledger/routes/transactions.py:list_transactions`, add to the `render_template` call:

```python
        projects=Project.query.filter_by(user_id=uid).order_by(Project.name).all(),
```

- [ ] **Step 4: Create the partial**

Create `gigledger/templates/transactions/_edit_modal.html`. It is the existing edit modal (index.html lines 252-311) plus the inventory block, the guidance line, and the shared script. Included inside `{% block content %}` by two pages, so its script runs before either page's `{% block scripts %}`.

```html
{#
  The edit-transaction modal and the kind-aware form logic it shares with the
  add modal. Included by transactions/index.html and inventory/index.html so
  the inventory page does not carry a second copy of a form that must post
  exactly what transactions.edit() expects.

  Expects `projects` and `currency` in context. Every id is `edit_<field>`;
  the add modal uses `modal_<field>`, and applyKind() works on either prefix.
#}
<div id="editModal" class="hidden fixed inset-0 z-50 flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/30 backdrop-blur-sm" onclick="document.getElementById('editModal').classList.add('hidden')"></div>
    <div class="relative glass-card rounded-3xl p-7 max-w-md w-full shadow-2xl max-h-[90vh] overflow-y-auto" style="background:rgba(255,255,255,0.9)">
        <div class="flex items-center justify-between mb-6">
            <h2 class="text-lg font-extrabold text-gray-900">Edit Transaction &#x270F;&#xFE0F;</h2>
            <button onclick="document.getElementById('editModal').classList.add('hidden')" class="text-gray-300 hover:text-gray-600 transition-colors p-1">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
            </button>
        </div>
        <form method="POST" id="editForm" class="space-y-4">
            {{ csrf_field() }}
            <input type="hidden" name="tx_id" id="edit_tx_id">
            <div>
                <label class="block text-sm font-semibold text-gray-600 mb-1.5">Type</label>
                <div class="flex gap-2">
                    <label class="flex-1 cursor-pointer">
                        <input type="radio" name="type" value="income" class="peer sr-only" id="edit_type_income" onchange="updateEditCategories()">
                        <div class="text-center py-2.5 rounded-xl border-2 border-gray-200/80 peer-checked:border-green-400 peer-checked:bg-green-50/80 peer-checked:text-green-700 text-sm font-semibold transition-all">Income</div>
                    </label>
                    <label class="flex-1 cursor-pointer">
                        <input type="radio" name="type" value="expense" class="peer sr-only" id="edit_type_expense" onchange="updateEditCategories()">
                        <div class="text-center py-2.5 rounded-xl border-2 border-gray-200/80 peer-checked:border-red-400 peer-checked:bg-red-50/80 peer-checked:text-red-700 text-sm font-semibold transition-all">Expense</div>
                    </label>
                    <label class="flex-1 cursor-pointer">
                        <input type="radio" name="type" value="inventory" class="peer sr-only" id="edit_type_inventory" onchange="updateEditCategories()">
                        <div class="text-center py-2.5 rounded-xl border-2 border-gray-200/80 peer-checked:border-amber-400 peer-checked:bg-amber-50/80 peer-checked:text-amber-700 text-sm font-semibold transition-all">Inventory</div>
                    </label>
                </div>
                <p class="text-[11px] text-gray-400 mt-1.5">To turn a purchase into an expense, or an expense into a purchase, delete it and add it again.</p>
            </div>
            <div id="edit_amount_section">
                <label class="block text-sm font-semibold text-gray-600 mb-1.5">Amount ({{ currency }})</label>
                <div class="relative">
                    <span class="absolute inset-y-0 left-0 pl-3 flex items-center text-gray-400 font-medium">{{ currency | currency_symbol }}</span>
                    <input type="number" name="amount" id="edit_amount" step="0.01" min="0.01" required class="candy-input block w-full pl-7 pr-3 py-2.5 text-gray-900 focus:outline-none">
                </div>
            </div>
            <div id="edit_inventory_section" class="hidden space-y-4">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-sm font-semibold text-gray-600 mb-1.5">Quantity</label>
                        <input type="number" name="quantity" id="edit_quantity" step="0.01" min="0.01" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-semibold text-gray-600 mb-1.5">Unit cost ({{ currency }})</label>
                        <input type="number" name="unit_cost" id="edit_unit_cost" step="0.01" min="0.01" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                    </div>
                </div>
                <div>
                    <label class="block text-sm font-semibold text-gray-600 mb-1.5">Bought for</label>
                    <select name="project_id" id="edit_project" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                        <option value="">General Inventory</option>
                        {% for p in projects %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}
                    </select>
                </div>
                <div class="flex items-center gap-2">
                    <input type="checkbox" name="is_consumable" id="edit_consumable" class="h-4 w-4 text-amber-600 border-gray-300 rounded focus:ring-amber-500">
                    <label for="edit_consumable" class="text-sm font-medium text-gray-600">Consumable &mdash; used up on a project, not returned</label>
                </div>
            </div>
            <div>
                <label class="block text-sm font-semibold text-gray-600 mb-1.5">Date</label>
                <input type="date" name="date" id="edit_date" required class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
            </div>
            <div>
                <label class="block text-sm font-semibold text-gray-600 mb-1.5">Category</label>
                <select name="category" id="edit_category" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none" onchange="showGuidance('edit')">
                </select>
                <p id="edit_category_guidance" class="hidden text-xs text-gray-400 mt-1.5 leading-relaxed"></p>
            </div>
            <div>
                <label class="block text-sm font-semibold text-gray-600 mb-1.5">Description</label>
                <input type="text" name="description" id="edit_description" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
            </div>
            <div id="edit_deductible_section" class="hidden">
                <div class="flex items-center gap-2">
                    <input type="checkbox" name="is_tax_deductible" id="edit_ded" class="h-4 w-4 text-brand-600 border-gray-300 rounded focus:ring-brand-500">
                    <label for="edit_ded" class="text-sm font-medium text-gray-600">Tax deductible &#x1F4B0;</label>
                </div>
            </div>
            <div class="flex gap-3 pt-2">
                <button type="button" onclick="document.getElementById('editModal').classList.add('hidden')" class="flex-1 py-3 px-4 glass-card rounded-xl text-sm font-semibold text-gray-600 hover:bg-white/80 transition-all">Cancel</button>
                <button type="submit" class="candy-btn flex-1 py-3 px-4 text-sm">Save Changes</button>
            </div>
        </form>
    </div>
</div>

<script>
    // Shared by the add and edit modals. One lookup keyed on the kind, not
    // an income/else branch: the else is what used to turn inventory into
    // expense (see the piece 2 spec).
    var kindCategories = {
        income: {{ current_user.get_income_categories() | tojson }},
        expense: {{ current_user.get_expense_categories() | tojson }},
        inventory: {{ current_user.get_inventory_categories() | tojson }}
    };
    var inventoryGuidance = {{ inventory_guidance | tojson }};

    function fillCategories(select, kind, selectedCat) {
        var cats = kindCategories[kind] || [];
        select.innerHTML = '';
        cats.forEach(function(cat) {
            var opt = document.createElement('option');
            opt.value = cat;
            opt.textContent = cat;
            if (cat === selectedCat) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function showGuidance(prefix) {
        var kind = checkedKind(prefix);
        var el = document.getElementById(prefix + '_category_guidance');
        var text = kind === 'inventory'
            ? inventoryGuidance[document.getElementById(prefix + '_category').value] : '';
        el.textContent = text || '';
        el.classList.toggle('hidden', !text);
    }

    function checkedKind(prefix) {
        var form = prefix === 'edit' ? '#editForm' : '#modalForm';
        var radio = document.querySelector(form + ' input[name="type"]:checked');
        return radio ? radio.value : '';
    }

    // Show the fields a kind needs and require only those. A hidden `required`
    // input blocks submission, so `required` follows visibility.
    function applyKind(prefix, kind, selectedCat) {
        var isInventory = kind === 'inventory';
        document.getElementById(prefix + '_amount_section').classList.toggle('hidden', isInventory);
        document.getElementById(prefix + '_amount').required = !isInventory;
        document.getElementById(prefix + '_inventory_section').classList.toggle('hidden', !isInventory);
        document.getElementById(prefix + '_quantity').required = isInventory;
        document.getElementById(prefix + '_unit_cost').required = isInventory;
        document.getElementById(prefix + '_deductible_section').classList.toggle('hidden', kind !== 'expense');
        if (kind !== 'expense') document.getElementById(prefix + '_ded').checked = false;
        fillCategories(document.getElementById(prefix + '_category'), kind, selectedCat);
        showGuidance(prefix);
    }

    function openEditModal(id, amount, type, date, category, description, deductible, item) {
        document.getElementById('edit_tx_id').value = id;
        document.getElementById('edit_amount').value = amount;
        document.getElementById('edit_date').value = date;
        document.getElementById('edit_description').value = description;

        var radio = document.querySelector('#editForm input[name="type"][value="' + type + '"]');
        if (radio) radio.checked = true;
        applyKind('edit', type, category);
        document.getElementById('edit_ded').checked = type === 'expense' && !!deductible;

        item = item || {};
        document.getElementById('edit_quantity').value = item.quantity == null ? '' : item.quantity;
        document.getElementById('edit_unit_cost').value = item.unit_cost == null ? '' : item.unit_cost;
        document.getElementById('edit_consumable').checked = !!item.is_consumable;
        document.getElementById('edit_project').value = item.project_id == null ? '' : String(item.project_id);

        document.getElementById('editForm').action = '{{ url_port("transactions.edit", id=0) }}'.replace('/0', '/' + id);
        document.getElementById('editModal').classList.remove('hidden');
    }

    function updateEditCategories() {
        applyKind('edit', checkedKind('edit'));
    }
</script>
```

- [ ] **Step 5: Rewire `transactions/index.html`**

(a) Filter (line 44, after the Expense option):

```html
                    <option value="inventory" {% if selected_type == 'inventory' %}selected{% endif %}>Inventory</option>
```

(b) Row badge (line 127): give inventory its colour:

```html
                            <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold {% if tx.is_income %}bg-green-50 text-green-600{% elif tx.is_inventory %}bg-amber-50 text-amber-700{% else %}bg-gray-100/80 text-gray-600{% endif %}">
```

(c) Edit button (line 157): add the item argument. The whole `onclick` becomes:

```html
                                <button onclick="openEditModal({{ tx.id|tojson|forceescape }}, {{ (tx.amount|abs)|tojson|forceescape }}, {{ tx.kind|tojson|forceescape }}, {{ tx.date.strftime('%Y-%m-%d')|tojson|forceescape }}, {{ (tx.category or '')|tojson|forceescape }}, {{ (tx.description or '')|tojson|forceescape }}, {{ tx.is_tax_deductible|tojson|forceescape }}, {{ (tx.inventory_item.form_values if tx.inventory_item else none)|tojson|forceescape }})" class="text-gray-300 hover:text-brand-500 transition-all p-1 hover:scale-110" title="Edit">
```

(d) Add modal. Add the third radio after the Expense radio (line 209-211):

```html
                    <label class="flex-1 cursor-pointer">
                        <input type="radio" name="type" value="inventory" class="peer sr-only" onchange="updateModalCategories()">
                        <div class="text-center py-2.5 rounded-xl border-2 border-gray-200/80 peer-checked:border-amber-400 peer-checked:bg-amber-50/80 peer-checked:text-amber-700 text-sm font-semibold transition-all">Inventory</div>
                    </label>
```

Wrap the amount `<div>` (lines 214-220) as `<div id="modal_amount_section">` and insert the inventory block directly after it — the same block as the partial with every `edit_` id replaced by `modal_`:

```html
            <div id="modal_inventory_section" class="hidden space-y-4">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-sm font-semibold text-gray-600 mb-1.5">Quantity</label>
                        <input type="number" name="quantity" id="modal_quantity" step="0.01" min="0.01" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-sm font-semibold text-gray-600 mb-1.5">Unit cost ({{ currency }})</label>
                        <input type="number" name="unit_cost" id="modal_unit_cost" step="0.01" min="0.01" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                    </div>
                </div>
                <div>
                    <label class="block text-sm font-semibold text-gray-600 mb-1.5">Bought for</label>
                    <select name="project_id" id="modal_project" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none">
                        <option value="">General Inventory</option>
                        {% for p in projects %}<option value="{{ p.id }}">{{ p.name }}</option>{% endfor %}
                    </select>
                </div>
                <div class="flex items-center gap-2">
                    <input type="checkbox" name="is_consumable" id="modal_consumable" class="h-4 w-4 text-amber-600 border-gray-300 rounded focus:ring-amber-500">
                    <label for="modal_consumable" class="text-sm font-medium text-gray-600">Consumable &mdash; used up on a project, not returned</label>
                </div>
            </div>
```

Category select (line 227): add `onchange="showGuidance('modal')"` and the guidance line after it:

```html
                <select name="category" id="modal_category" class="candy-input block w-full px-3 py-2.5 text-gray-900 focus:outline-none" onchange="showGuidance('modal')">
                </select>
                <p id="modal_category_guidance" class="hidden text-xs text-gray-400 mt-1.5 leading-relaxed"></p>
```

Rename the deductible checkbox id `m_ded` → `modal_ded` (the `<input id>` and the `<label for>`; there are 4 occurrences of `m_ded` in the file — the input, the label, and two in the script; change all).

(e) Replace the entire edit modal block (`<!-- Edit Transaction Modal -->` through its closing `</div>`, lines 251-311) with:

```html
{% include 'transactions/_edit_modal.html' %}
```

(f) Script block. Delete `var incomeCategories`, `var expenseCategories` (they now live in the partial as `kindCategories`). Replace `updateModalCategories` with:

```js
    function updateModalCategories() {
        applyKind('modal', checkedKind('modal'));
        document.getElementById('modal_tax_impact').classList.add('hidden');
        updateModalTaxImpact();
    }
```

Delete `openEditModal` and `updateEditCategories` from this script block (they are in the partial). Keep `updateModalTaxImpact`, the `modal_amount` input listener, and the `updateModalCategories();` call. `updateModalTaxImpact` reads `modal_ded` (renamed).

- [ ] **Step 6: Export Type column**

`gigledger/templates/transactions/export.html`, the table:

```html
    <thead><tr><th>Date</th><th>Type</th><th>Description</th><th>Category</th><th style="text-align:right">Amount</th><th style="text-align:center">Deductible</th></tr></thead>
    <tbody>{% for tx in transactions %}<tr>
            <td>{{ tx.date.strftime('%b %d, %Y') }}</td>
            <td>{{ tx.kind_label }}</td>
            <td>{{ tx.description or '-' }}</td>
```

(the remaining cells unchanged.)

- [ ] **Step 7: Run the tests and look at the page**

Run: `python -m pytest -q`
Expected: all pass — including `test_template_escaping.py` (every new `onclick` argument carries both filters) and `test_the_edit_modal_receives_the_stored_kind` from piece 1.

Then a manual check: `source venv/bin/activate && python run.py` (or however `run.py` starts; check `head run.py`), log in as `demo@gigledger.com` / `demo1234`, open Transactions → Add: click Inventory — amount hides, quantity/unit cost/Bought for/Consumable appear, categories switch to the seven, picking one shows guidance. Add "2 × 490 sofa" to General Inventory. It appears with an amber category badge. Click Edit on it — the Inventory radio is checked and the item fields are prefilled. Change the date only, Save — it stays Inventory. Click Edit on an expense — Income/Expense behave as before. Stop the server.

- [ ] **Step 8: Commit**

```bash
git add gigledger/templates/transactions/_edit_modal.html gigledger/templates/transactions/index.html gigledger/templates/transactions/export.html gigledger/routes/transactions.py gigledger/app.py tests/test_inventory.py
git commit -m "Give the transactions page an Inventory kind

Third radio on both modals, revealing quantity, unit cost, project and
consumable and hiding amount and deductible. The edit modal moves to a
shared partial so the Inventory page can include it, and its JavaScript
looks up the kind instead of branching income/else - the else is what
made a date edit turn a purchase into an expense. The Type filter and
the HTML export learn the third kind.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The Inventory page

**Files:**
- Create: `gigledger/routes/inventory.py`
- Create: `gigledger/templates/inventory/index.html`
- Modify: `gigledger/app.py` (import + register `inventory_bp`, lines ~161 and ~173)
- Modify: `gigledger/templates/base.html` (nav after Projects: lines ~608-611 desktop and ~724-727 mobile)
- Test: `tests/test_inventory.py`

**Interfaces:**
- Consumes: `InventoryItem`, `Transaction`, `Project` (Task 1); partial `transactions/_edit_modal.html` (Task 5), which needs `projects` and `currency` in context.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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


def test_the_inventory_page_shows_only_the_current_users_items(pool):
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
    assert 'Theirs' not in page
    assert '$1,350.00' in page


def test_the_inventory_page_has_an_empty_state_and_the_edit_modal(app):
    page = authenticated_client(app).get('/inventory/').get_data(as_text=True)
    assert 'No inventory yet' in page
    assert 'id="editModal"' in page


def test_the_nav_links_to_inventory(app):
    page = authenticated_client(app).get('/transactions').get_data(as_text=True)
    assert 'href="/inventory/"' in page
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k "inventory_page or nav"`
Expected: 404s / assertion failures.

- [ ] **Step 3: The blueprint**

Create `gigledger/routes/inventory.py`:

```python
"""The asset pool: what was bought as inventory and where it was bought for.

Read-only. Every write goes through transactions.py, so money has one
authorisation path and this page cannot drift from the ledger. Movements,
quantity remaining and cost recognition are piece 3.
"""
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from ..models import InventoryItem, Transaction, Project

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')


def _filtered(items, args):
    project = args.get('project', '')
    if project == 'general':
        items = [i for i in items if i.project_id is None]
    elif project:
        try:
            pid = int(project)
            items = [i for i in items if i.project_id == pid]
        except ValueError:
            pass

    category = args.get('category', '')
    if category:
        items = [i for i in items if i.transaction.category == category]
    return items


@inventory_bp.route('/')
@login_required
def index():
    uid = current_user.id
    everything = (InventoryItem.query.filter_by(user_id=uid)
                  .join(InventoryItem.transaction)
                  .order_by(Transaction.date.desc()).all())

    # The cards describe the whole pool; the filters narrow only the table.
    asset_value = sum(i.total_cost for i in everything)
    on_hand = sum(i.total_cost for i in everything if i.project_id is None)

    return render_template('inventory/index.html',
        items=_filtered(everything, request.args),
        asset_value=asset_value, on_hand=on_hand,
        on_projects=asset_value - on_hand,
        projects=Project.query.filter_by(user_id=uid).order_by(Project.name).all(),
        categories=sorted(current_user.get_inventory_categories()),
        selected_project=request.args.get('project', ''),
        selected_category=request.args.get('category', ''),
        currency=current_user.currency)
```

Register it in `gigledger/app.py`: add `from .routes.inventory import inventory_bp` after the recurring import and `app.register_blueprint(inventory_bp)` after `recurring_bp`.

- [ ] **Step 4: The template**

Create `gigledger/templates/inventory/index.html`:

```html
{% extends "base.html" %}
{% block title %}Inventory - GigLedger{% endblock %}
{% block content %}
<div class="space-y-6">
    <div class="fade-up">
        <h1 class="text-2xl font-extrabold bg-gradient-to-r from-gray-800 to-gray-600 bg-clip-text text-transparent">Inventory</h1>
        <p class="text-sm text-gray-400 mt-1 font-medium">Assets you own. Bought as inventory, not counted as an expense. Record a purchase from the Transactions page.</p>
    </div>

    <!-- Pool -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 fade-up fade-up-delay-1">
        <div class="glass-card p-5">
            <p class="text-xs text-amber-500 font-semibold uppercase tracking-wider">Asset value</p>
            <p class="text-2xl font-extrabold text-amber-600 mt-1">{{ currency | currency_symbol }}{{ asset_value | money }}</p>
        </div>
        <div class="glass-card p-5">
            <p class="text-xs text-gray-400 font-semibold uppercase tracking-wider">On hand</p>
            <p class="text-2xl font-extrabold text-gray-800 mt-1">{{ currency | currency_symbol }}{{ on_hand | money }}</p>
            <p class="text-xs text-gray-400 mt-1">General Inventory</p>
        </div>
        <div class="glass-card p-5">
            <p class="text-xs text-gray-400 font-semibold uppercase tracking-wider">On projects</p>
            <p class="text-2xl font-extrabold text-gray-800 mt-1">{{ currency | currency_symbol }}{{ on_projects | money }}</p>
            <p class="text-xs text-gray-400 mt-1">Bought for a job</p>
        </div>
    </div>

    <!-- Filters -->
    <div class="glass-card p-4 fade-up fade-up-delay-2">
        <form method="GET" action="{{ url_port('inventory.index') }}" class="flex flex-wrap gap-3 items-end">
            <div>
                <label class="block text-xs font-semibold text-gray-400 mb-1">Location</label>
                <select name="project" class="candy-input px-3 py-2 text-sm text-gray-900 focus:outline-none">
                    <option value="">All</option>
                    <option value="general" {% if selected_project == 'general' %}selected{% endif %}>General Inventory</option>
                    {% for p in projects %}
                    <option value="{{ p.id }}" {% if selected_project == p.id|string %}selected{% endif %}>{{ p.name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div>
                <label class="block text-xs font-semibold text-gray-400 mb-1">Category</label>
                <select name="category" class="candy-input px-3 py-2 text-sm text-gray-900 focus:outline-none">
                    <option value="">All</option>
                    {% for cat in categories %}
                    <option value="{{ cat }}" {% if selected_category == cat %}selected{% endif %}>{{ cat }}</option>
                    {% endfor %}
                </select>
            </div>
            <button type="submit" class="candy-btn px-5 py-2 text-sm">Filter</button>
        </form>
    </div>

    <!-- Items -->
    <div class="glass-card overflow-hidden fade-up fade-up-delay-3">
        {% if items %}
        <div class="overflow-x-auto max-h-[600px] overflow-y-auto">
            <table class="w-full">
                <thead class="sticky top-0 bg-white/80 backdrop-blur-lg border-b border-gray-100/50 z-10">
                    <tr>
                        <th class="px-5 py-3 text-left text-xs font-bold text-gray-400 uppercase tracking-wider">Date</th>
                        <th class="px-5 py-3 text-left text-xs font-bold text-gray-400 uppercase tracking-wider">Item</th>
                        <th class="px-5 py-3 text-left text-xs font-bold text-gray-400 uppercase tracking-wider">Category</th>
                        <th class="px-5 py-3 text-right text-xs font-bold text-gray-400 uppercase tracking-wider">Qty</th>
                        <th class="px-5 py-3 text-right text-xs font-bold text-gray-400 uppercase tracking-wider">Unit cost</th>
                        <th class="px-5 py-3 text-right text-xs font-bold text-gray-400 uppercase tracking-wider">Total</th>
                        <th class="px-5 py-3 text-center text-xs font-bold text-gray-400 uppercase tracking-wider">Use</th>
                        <th class="px-5 py-3 text-left text-xs font-bold text-gray-400 uppercase tracking-wider">Location</th>
                        <th class="px-5 py-3 text-center text-xs font-bold text-gray-400 uppercase tracking-wider">Actions</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-gray-100/50">
                    {% for item in items %}{% set tx = item.transaction %}
                    <tr class="hover:bg-white/50 transition-all">
                        <td class="px-5 py-3.5 text-sm text-gray-500 whitespace-nowrap font-medium">{{ tx.date.strftime('%b %d, %Y') }}</td>
                        <td class="px-5 py-3.5 text-sm text-gray-800 font-semibold max-w-xs truncate">{{ tx.description or 'No description' }}</td>
                        <td class="px-5 py-3.5"><span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-amber-50 text-amber-700">{{ tx.category or 'Other' }}</span></td>
                        <td class="px-5 py-3.5 text-sm text-right text-gray-700 font-medium">{{ item.quantity | decimal1 if item.quantity != item.quantity|int else item.quantity|int }}</td>
                        <td class="px-5 py-3.5 text-sm text-right text-gray-700 font-medium whitespace-nowrap">{{ currency | currency_symbol }}{{ item.unit_cost | money }}</td>
                        <td class="px-5 py-3.5 text-sm text-right text-gray-900 font-extrabold whitespace-nowrap">{{ currency | currency_symbol }}{{ item.total_cost | money }}</td>
                        <td class="px-5 py-3.5 text-center"><span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold {% if item.is_consumable %}bg-orange-50 text-orange-600{% else %}bg-sky-50 text-sky-600{% endif %}">{{ item.usage_label }}</span></td>
                        <td class="px-5 py-3.5 text-sm text-gray-600 font-medium whitespace-nowrap">
                            {% if item.project %}<span class="inline-block w-2 h-2 rounded-full mr-1.5" style="background:{{ item.project.color }}"></span>{% endif %}{{ item.location_label }}
                        </td>
                        <td class="px-5 py-3.5 text-center">
                            <button onclick="openEditModal({{ tx.id|tojson|forceescape }}, {{ (tx.amount|abs)|tojson|forceescape }}, {{ tx.kind|tojson|forceescape }}, {{ tx.date.strftime('%Y-%m-%d')|tojson|forceescape }}, {{ (tx.category or '')|tojson|forceescape }}, {{ (tx.description or '')|tojson|forceescape }}, {{ tx.is_tax_deductible|tojson|forceescape }}, {{ item.form_values|tojson|forceescape }})" class="text-gray-300 hover:text-brand-500 transition-all p-1 hover:scale-110" title="Edit">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                            </button>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <div class="text-center py-14">
            <p class="text-sm font-semibold text-gray-500">No inventory yet</p>
            <p class="text-xs text-gray-400 mt-1">Add a transaction and choose Inventory to record a purchase.</p>
        </div>
        {% endif %}
    </div>
</div>

{% include 'transactions/_edit_modal.html' %}
{% endblock %}
```

Check `decimal1` exists as a filter (`grep -n decimal1 gigledger/app.py` showed it does). If the quantity expression is awkward, simplify to `{{ item.quantity | decimal1 }}` — the test does not assert the quantity format.

- [ ] **Step 5: The nav**

In `gigledger/templates/base.html`, after the desktop Projects link (the `</a>` at ~line 611), add:

```html
                <a href="{{ url_port('inventory.index') }}" class="nav-link icon-bounce flex items-center gap-3 px-4 py-3 text-sm font-semibold {% if request.endpoint and 'inventory' in request.endpoint %}bg-brand-50 text-brand-700 shadow-sm{% else %}text-gray-500 hover:bg-white/60 hover:text-gray-800{% endif %}">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" /></svg>
                    Inventory
                </a>
```

After the mobile Projects link (the `</a>` at ~line 727):

```html
                    <a href="{{ url_port('inventory.index') }}" class="flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-2xl text-xs transition-all whitespace-nowrap {% if request.endpoint and 'inventory' in request.endpoint %}bg-brand-50 text-brand-600 font-bold{% else %}text-gray-400{% endif %}">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" /></svg>
                        Inventory
                    </a>
```

- [ ] **Step 6: Run the tests and look at the page**

Run: `python -m pytest -q`
Expected: all pass.

Manual: start the server, open Inventory. Three cards, the table, filters work, Edit opens the modal prefilled and Save returns to Inventory (edit() redirects to the referrer). Empty state when filtered to nothing.

- [ ] **Step 7: Commit**

```bash
git add gigledger/routes/inventory.py gigledger/templates/inventory/index.html gigledger/app.py gigledger/templates/base.html tests/test_inventory.py
git commit -m "Add the Inventory page

Read-only view over the ledger: asset value, on hand, on projects, and
every purchase with its quantity, unit cost, use and location. Writes
stay in transactions.py so money has one authorisation path; the page
includes the shared edit modal rather than a copy.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Inventory on the project detail page

**Files:**
- Modify: `gigledger/routes/projects.py:detail` (~line 278-291) and its imports (line ~1-10)
- Modify: `gigledger/templates/projects/detail.html` (after the Documents card, before the closing `</div>` of the page column at ~line 137)
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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
```

`projects_bp` has `url_prefix='/projects'`, so detail is `/projects/<id>`.

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k project_detail`
Expected: 2 failures.

- [ ] **Step 3: The route**

Add `InventoryItem, Transaction` to the models import at the top of `gigledger/routes/projects.py` (check what it imports already). In `detail()`, add two arguments to `render_template`:

```python
        inventory_items=(InventoryItem.query.filter_by(project_id=project.id)
                         .join(InventoryItem.transaction)
                         .order_by(Transaction.date.desc()).all()),
```

and, computed first:

```python
    inventory_items = (InventoryItem.query.filter_by(project_id=project.id)
                       .join(InventoryItem.transaction)
                       .order_by(Transaction.date.desc()).all())
    return render_template('projects/detail.html',
        project=project,
        inventory_items=inventory_items,
        inventory_total=sum(i.total_cost for i in inventory_items),
        ...  # the existing arguments unchanged
```

(`_owned_project` has already checked ownership; items are scoped by the project.)

- [ ] **Step 4: The section**

In `gigledger/templates/projects/detail.html`, immediately after the Documents card's closing `</div>` (the one following `{% endif %}` at ~line 136) and before the outer column's `</div>`:

```html
    <!-- Inventory -->
    <div class="glass-card p-5 fade-up fade-up-delay-4">
        <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-bold text-gray-400 uppercase tracking-wider">Inventory</h2>
            {% if inventory_items %}<span class="text-sm font-extrabold text-amber-600">{{ currency | currency_symbol }}{{ inventory_total | money }}</span>{% endif %}
        </div>
        {% if inventory_items %}
        <div class="divide-y divide-gray-100/50">
            {% for item in inventory_items %}
            <div class="flex items-center justify-between py-2.5 gap-3">
                <div class="min-w-0">
                    <p class="text-sm font-semibold text-gray-800 truncate">{{ item.transaction.description or 'No description' }}</p>
                    <p class="text-xs text-gray-400">{{ item.transaction.date.strftime('%b %d, %Y') }} &middot; {{ item.transaction.category or 'Other' }} &middot; {{ item.usage_label }}</p>
                </div>
                <div class="text-right whitespace-nowrap">
                    <p class="text-sm font-bold text-gray-800">{{ currency | currency_symbol }}{{ item.total_cost | money }}</p>
                    <p class="text-xs text-gray-400">{{ item.quantity | decimal1 }} &times; {{ currency | currency_symbol }}{{ item.unit_cost | money }}</p>
                </div>
            </div>
            {% endfor %}
        </div>
        <p class="text-[11px] text-gray-400 mt-3">Assets bought for this project. Not yet a project cost - that arrives when items are used.</p>
        {% else %}
        <p class="text-sm text-gray-400">No inventory bought for this project.</p>
        {% endif %}
    </div>
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -q`
Expected: all pass (`test_documents.py` and `test_document_sharing.py` render this page and must still).

- [ ] **Step 6: Commit**

```bash
git add gigledger/routes/projects.py gigledger/templates/projects/detail.html tests/test_inventory.py
git commit -m "List a project's inventory on its detail page

What was bought for the job and what it cost, with a note that it is
not yet a project cost. Deleting the project already returns its items
to General Inventory.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Settings — the third category block

**Files:**
- Modify: `gigledger/routes/settings.py` (imports line 3; `index()` lines 17-27; category routes lines 57-118; `reset_categories` lines 144-151)
- Modify: `gigledger/routes/recurring.py:53` (`get_all_categories(kinds={INCOME, EXPENSE})`)
- Modify: `gigledger/templates/settings/index.html` (new block after the Expense block ending ~line 241)
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inventory.py`:

```python
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
        assert 'Appliances' in User.query.get(demo_user_id(app)).get_inventory_categories()
    client.post('/settings/categories/inventory/delete', data={'category_name': 'Appliances'})
    with app.app_context():
        assert 'Appliances' not in User.query.get(demo_user_id(app)).get_inventory_categories()


@pytest.mark.parametrize('kind,default', [
    ('income', 'Client Payment'), ('expense', 'Software'), ('inventory', 'Seating')])
def test_a_default_category_cannot_be_removed(app, kind, default):
    body = authenticated_client(app).post(
        f'/settings/categories/{kind}/delete', data={'category_name': default},
        follow_redirects=True).get_data(as_text=True)
    assert 'Default categories' in body
    with app.app_context():
        user = User.query.get(demo_user_id(app))
        assert default in user.get_all_categories()


def test_reset_clears_the_inventory_list_too(app):
    client = authenticated_client(app)
    client.post('/settings/categories/inventory/add', data={'category_name': 'Appliances'})
    client.post('/settings/categories/reset')
    with app.app_context():
        user = User.query.get(demo_user_id(app))
        assert user.custom_inventory_categories == ''
        assert user.get_inventory_categories() == DEFAULT_INVENTORY_CATEGORIES


def test_the_recurring_page_does_not_offer_inventory_categories(app):
    page = authenticated_client(app).get('/recurring/').get_data(as_text=True)
    assert 'Casegoods' not in page
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_inventory.py -q -k "settings or category or reset or recurring_page"`
Expected: 404s and assertion failures. (`test_the_recurring_page…` may already pass; that is fine — it pins the kind-aware call.)

- [ ] **Step 3: Shared helpers, six thin routes**

In `gigledger/routes/settings.py`, replace the import on line 3:

```python
from ..models import (db, DEFAULT_INCOME_CATEGORIES, DEFAULT_EXPENSE_CATEGORIES,
                      DEFAULT_INVENTORY_CATEGORIES)
```

Extend `index()`'s `render_template` with:

```python
        inventory_categories=current_user.get_inventory_categories(),
        default_inventory_categories=DEFAULT_INVENTORY_CATEGORIES,
```

Replace the four category routes (lines 57-118) with:

```python
# One list per kind. The routes stay separate endpoints - templates and the
# CSRF walk name them - but the behaviour is written once.
CATEGORY_LISTS = {
    'income': ('Income', 'custom_income_categories', DEFAULT_INCOME_CATEGORIES),
    'expense': ('Expense', 'custom_expense_categories', DEFAULT_EXPENSE_CATEGORIES),
    'inventory': ('Inventory', 'custom_inventory_categories', DEFAULT_INVENTORY_CATEGORIES),
}


def _current(kind):
    return {'income': current_user.get_income_categories,
            'expense': current_user.get_expense_categories,
            'inventory': current_user.get_inventory_categories}[kind]()


def _add_category(kind):
    label, column, _ = CATEGORY_LISTS[kind]
    name = request.form.get('category_name', '').strip()
    if not name:
        flash('Category name cannot be empty.', 'error')
    elif name in _current(kind):
        flash(f'Category "{name}" already exists.', 'error')
    else:
        setattr(current_user, column, ','.join(_current(kind) + [name]))
        db.session.commit()
        flash(f'{label} category "{name}" added!', 'success')
    return redirect(url_for('settings.index'))


def _delete_category(kind):
    label, column, defaults = CATEGORY_LISTS[kind]
    name = request.form.get('category_name', '').strip()
    cats = _current(kind)
    if name in defaults:
        # The UI offers no button for these; the route agrees.
        flash('Default categories cannot be removed.', 'error')
    elif name in cats:
        cats.remove(name)
        setattr(current_user, column, ','.join(cats) if cats else '')
        db.session.commit()
        flash(f'{label} category "{name}" removed.', 'success')
    else:
        flash(f'Category "{name}" not found.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/categories/income/add', methods=['POST'])
@login_required
def add_income_category():
    return _add_category('income')


@settings_bp.route('/settings/categories/income/delete', methods=['POST'])
@login_required
def delete_income_category():
    return _delete_category('income')


@settings_bp.route('/settings/categories/expense/add', methods=['POST'])
@login_required
def add_expense_category():
    return _add_category('expense')


@settings_bp.route('/settings/categories/expense/delete', methods=['POST'])
@login_required
def delete_expense_category():
    return _delete_category('expense')


@settings_bp.route('/settings/categories/inventory/add', methods=['POST'])
@login_required
def add_inventory_category():
    return _add_category('inventory')


@settings_bp.route('/settings/categories/inventory/delete', methods=['POST'])
@login_required
def delete_inventory_category():
    return _delete_category('inventory')
```

In `reset_categories`, add:

```python
    current_user.custom_inventory_categories = ''
```

In `gigledger/routes/recurring.py:53`, change the import to include `INCOME, EXPENSE` (already imported) and the call to:

```python
        user_categories=current_user.get_all_categories(kinds={INCOME, EXPENSE}),
```

- [ ] **Step 4: The Settings block**

In `gigledger/templates/settings/index.html`, after the Expense Categories card (its closing `</div>` at ~line 241) and before `<!-- Reset categories -->`:

```html
    <!-- Inventory categories -->
    <div class="glass-card p-6 fade-up fade-up-delay-5">
        <div class="flex items-center justify-between mb-2">
            <h2 class="text-lg font-extrabold text-gray-900">Inventory Categories</h2>
            <span class="text-xs text-gray-300 font-medium">Assets, not expenses</span>
        </div>
        <p class="text-sm text-gray-400 mb-4 font-medium">Add or remove categories for inventory you buy and hold. The notes under each default say what belongs there.</p>
        <form method="POST" action="{{ url_port('settings.add_inventory_category') }}" class="flex gap-2 mb-4">
            {{ csrf_field() }}
            <input type="text" name="category_name" placeholder="New inventory category..." required class="candy-input flex-1 px-4 py-2.5 text-gray-900 focus:outline-none text-sm">
            <button type="submit" class="candy-btn px-4 py-2.5 text-sm">Add</button>
        </form>
        <div class="space-y-2">
            {% for cat in inventory_categories %}
            <div class="flex items-start gap-3">
                <div class="inline-flex items-center gap-1.5 px-3 py-1.5 bg-amber-50/80 text-amber-700 rounded-xl text-sm font-bold whitespace-nowrap">
                    {{ cat }}
                    {% if cat not in default_inventory_categories %}
                    <form method="POST" action="{{ url_port('settings.delete_inventory_category') }}" class="inline" onsubmit="return confirm({{ ('Remove ' ~ cat ~ '?')|tojson|forceescape }})">
                        {{ csrf_field() }}
                        <input type="hidden" name="category_name" value="{{ cat }}">
                        <button type="submit" class="text-amber-300 hover:text-amber-700 transition-colors ml-1">
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
                        </button>
                    </form>
                    {% else %}
                    <span class="text-amber-200 ml-1">&#8226;</span>
                    {% endif %}
                </div>
                {% if inventory_guidance[cat] %}<p class="text-xs text-gray-400 leading-relaxed pt-1.5">{{ inventory_guidance[cat] }}</p>{% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
```

Also update the Reset card's description text (~line 245) from "Reset all categories to defaults." to cover three lists if it names two; leave it if it already says "all".

- [ ] **Step 5: Run the tests**

Run: `python -m pytest -q`
Expected: all pass, `test_csrf.py` included (six category POST routes now, all protected).

- [ ] **Step 6: Commit**

```bash
git add gigledger/routes/settings.py gigledger/routes/recurring.py gigledger/templates/settings/index.html tests/test_inventory.py
git commit -m "Manage inventory categories in Settings

A third block behaving as the other two, with the taxonomy's guidance
shown under each default so it is discoverable where it is managed. The
add and delete behaviour is written once for all three lists, and a
default category can no longer be removed by a crafted POST. The
recurring page asks for two kinds' categories, not three.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: ADR-0011, glossary, README, spec status

**Files:**
- Create: `docs/adr/0011-inventory-purchases-as-ledger-lines.md`
- Modify: `docs/adr/README.md` (table, append a row)
- Modify: `docs/glossary.md` (new `## Finance` section before `## Deployment`)
- Modify: `README.md` (Transactions feature list ~line 55; add an Inventory feature block)
- Modify: `docs/superpowers/specs/2026-09-15-inventory-purchases-design.md` (Status line)

- [ ] **Step 1: Write ADR-0011**

Create `docs/adr/0011-inventory-purchases-as-ledger-lines.md`:

```markdown
# 0011. Inventory purchases are ledger lines with a linked asset row

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

ADR-0010 made `inventory` a representable kind and kept it out of every cost
total. Nothing could create one. An inventory purchase also carries facts a
ledger line does not: a quantity, a unit cost, whether the item is consumed or
reused, and what it was bought for - a project, or stock held for the next
job. Piece 3 will add movements: an item placed on a project, used up,
returned. Those facts and that lifecycle needed a home.

Two shapes were open. Nullable per-kind columns on `Transaction`, the pattern
ADR-0006 chose for `ProjectDocument`. Or a separate table, one row per
purchase, linked back to it.

## Decision

A separate table, `inventory_items`, 1:1 with its purchase `Transaction`
(`transaction_id` is unique). The transaction keeps description, category,
date and amount - ledger facts. The item holds quantity, unit cost,
`is_consumable`, and `project_id`, where NULL means General Inventory.

`amount` is derived: always `-(quantity × unit_cost)`, written by the route,
never posted by the form. The ledger and the pool cannot disagree.

Every write goes through `transactions.py`. The `/inventory` page and the
project detail section are read-only views over the ledger.

A kind change into or out of `inventory` by editing is refused. Delete and
re-add.

## Consequences

**Why not ADR-0006's shape.** That ADR's argument was that an upload and a
link differ only in how content is fetched and every other operation -
listing, sharing, deleting, authorising - is identical, so one table with a
discriminator beats two tables and two copies of the authorisation check. An
inventory item is placed, consumed and returned; a ledger line never is.
Different lifecycle, different table. Piece 3's `inventory_movements` will
reference an item, which reads correctly; `movements.transaction_id` would
not.

**One item per transaction.** A receipt with six chairs and two lamps is two
transactions. The alternative - line items under one transaction, like
`Invoice → InvoiceLineItem` - would make `amount` a sum, the edit modal a
line-item editor, and a transaction's category ambiguous when its items span
categories.

**The refused kind change is a wall, not a convenience.** Allowing it means
creating or orphaning an item mid-edit. Refusing it costs one guard clause
and gives piece 3 a guarantee it would otherwise have to build: a placed item
cannot quietly become an expense.

**Deleting a project returns its items to General Inventory.** The
`Project → inventory_items` relationship has no delete cascade; SQLAlchemy
nulls the foreign key. Assets that are still owned are not destroyed with the
job they were bought for.

**One sign rule.** `transactions.py` now applies the rule `recurring.py`
already did: income is cash in, every other kind is cash out. The two-branch
coercion it replaced left a third kind's sign to whatever was posted.

**Monthly commitment is cash.** Recorded in ADR-0010's kind-blind list.

**Recurring inventory is deferred.** The recurring form offers income and
expense. `RecurringTransaction.kind` already holds `inventory` and the
commitment rule already counts it, so adding the form later is additive.

**The dashboard quick-add offers two kinds.** It is the fast path; an
inventory purchase has four more fields. Its script is a copy of the
transactions modal's, and this decision declines to grow the copy.
```

- [ ] **Step 2: Index it**

Append to the table in `docs/adr/README.md`:

```markdown
| [0011](0011-inventory-purchases-as-ledger-lines.md) | Inventory purchases are ledger lines with a linked asset row | Accepted |
```

- [ ] **Step 3: Glossary**

In `docs/glossary.md`, insert before `## Deployment`:

```markdown
## Finance

### Transaction Kind

`Transaction.kind` and `RecurringTransaction.kind` — `income`, `expense` or
`inventory`. Stored, never derived from the sign of `amount`: a sign carries
one bit and the vocabulary has three members. The sign says which way the money
moved; the kind says what the movement was. [ADR-0010](adr/0010-classify-transactions-by-kind.md).

### Cost Kind

A kind that reduces profit. `COST_KINDS = {EXPENSE}` in `models.py` is the one
place that answers the question; inventory's absence from the set is the entire
behaviour that keeps it out of every expense total.

### Kind-Blind Figure

A figure that sums every kind on purpose because it measures cash, not cost:
Safe to Spend, Runway, and Monthly Commitments. Each looks like a missed
conversion to kind and is not one. Listed in ADR-0010 so nobody "fixes" them.

### Inventory Item

`InventoryItem` — the asset half of an inventory purchase, 1:1 with its
`Transaction`: quantity, unit cost, consumable or reusable, and where it was
bought for. The transaction's `amount` is always `-(quantity × unit_cost)`.
[ADR-0011](adr/0011-inventory-purchases-as-ledger-lines.md).

### General Inventory

Stock held for no particular job. Represented as `InventoryItem.project_id IS
NULL`, not as a sentinel project. Items whose project is deleted return here.

### Consumable / Reusable

`InventoryItem.is_consumable`. A consumable becomes a cost when used on a
project; a reusable is placed on a project and comes back, staying an asset
throughout. Both are piece 3 behaviour; piece 2 only records the flag.
```

- [ ] **Step 4: README**

In `README.md`, add to the Transactions bullet list (after "**Edit & Delete**"):

```markdown
- **Three kinds** — Income, Expense, and Inventory: purchases held as assets that never touch your expense totals
```

Add a new feature block after the Transactions section (before `### 🧾 Invoicing`):

```markdown
### 📦 Inventory
- **Asset pool** — Purchases recorded as Inventory stay out of expenses, deductions and profit while their cash still leaves the balance
- **Quantity, unit cost, consumable or reusable** — Recorded per purchase; the amount is always quantity × unit cost
- **Bought for a project, or General Inventory** — Each purchase names a project or sits on hand; project pages list what was bought for them
- **Inventory page** — Asset value, on hand vs. on projects, filter by location and category
- **Seven default categories with guidance** — Casegoods, Seating, Lighting, Soft Goods, Wall Decor, Tabletop, Outdoor; editable in Settings
```

Update the Monthly Commitments bullet (~line 44):

```markdown
- **Monthly Commitments** — Everything that leaves the account monthly from active recurring transactions, inventory orders included
```

- [ ] **Step 5: Spec status**

In `docs/superpowers/specs/2026-09-15-inventory-purchases-design.md`, change the Status line to `- **Status:** Implemented 2026-09-15`.

- [ ] **Step 6: Full suite, then commit**

Run: `python -m pytest -q`
Expected: all pass. Note the count in the commit body.

```bash
git add docs/adr/0011-inventory-purchases-as-ledger-lines.md docs/adr/README.md docs/glossary.md README.md docs/superpowers/specs/2026-09-15-inventory-purchases-design.md
git commit -m "Record the inventory purchase decisions

ADR-0011 for the 1:1 asset row over per-kind columns, the derived amount,
the refused kind change and NULL-as-General-Inventory; a Finance section
in the glossary; the README learns the third kind.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Model, categories, migration — Task 1. Sign rule, `add()`, deductible forced off, project validation — Task 2. Edit rules and the blocker regression test — Task 3. Monthly commitment + ADR-0010 — Task 4. Third radio, inventory block, guidance, JS lookup, shared partial, filter option, HTML export Type column — Task 5. Inventory page, nav — Task 6. Project detail section — Task 7. Settings block, reset, kind-aware recurring dropdown — Task 8. ADR-0011, glossary, README — Task 9. Dashboard quick-add unchanged and recurring deferred — by omission, recorded in ADR-0011. Characterization unedited — Global Constraints. Every test in the spec's Testing section has a test above.

**Type consistency.** `_inventory_fields` returns `(quantity, unit_cost, is_consumable, project_id)` and both Task 2 and Task 3 unpack in that order. `InventoryItem.form_values` keys (`quantity`, `unit_cost`, `is_consumable`, `project_id`) match what `openEditModal` reads. Element ids follow `<prefix>_<field>` in both the partial and the add modal; the deductible checkbox is `modal_ded` / `edit_ded` after the rename. `get_all_categories(kinds=...)` takes a set; Task 8 passes `{INCOME, EXPENSE}`. `pool` fixture returns `(app, pid, pname)` and Tasks 6 and 7 unpack it that way.

**Known judgement calls left to the implementer.** Line numbers are from the tree at commit `f70ed4a` and drift as earlier tasks land; anchor on the quoted code. The quantity display expression in Task 6 may be simplified to `| decimal1`.
