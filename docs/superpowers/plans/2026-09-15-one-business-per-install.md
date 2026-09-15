# One Business Per Install — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every admin login sees and edits the same clients, projects, invoices, transactions, documents and settings; admins are added by invitation from Settings; public sign-up is removed; a fresh install starts at `/setup`.

**Architecture:** A single-row `Business` model takes the business-wide settings off `User`; `Business.get()` is the one seam that fetches it, and a context processor injects it into every template as `business`. Every `filter_by(user_id=current_user.id)` loses its `user_id` clause so `@login_required` is the whole access rule; `user_id` columns stay as a passive "created by" stamp. A new `gigledger/team.py` owns admin invites (a copy of the portal-invite pattern) and the first-run setup rule.

**Tech Stack:** Flask 3, Flask-SQLAlchemy, Flask-Login, Flask-Bcrypt, Flask-WTF (CSRF), SQLite, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-one-business-per-install-design.md`

## Global Constraints

- Python 3.13, run tests with `venv/bin/python -m pytest tests -q` from the repo root. Every task ends with the full suite green.
- Never run `create_app()` against the real `gigledger.db` in a test: every test app patches `gigledger.app.DB_PATH` (and `gigledger.documents.UPLOAD_ROOT` where documents are involved) to a `tmp_path`. `tests/conftest.py` (Task 1) sets `SEED_DEMO=1` for every test so the existing fixtures that rely on seeded rows keep working.
- No hand-built HTML in routes: pages are rendered from templates (`tests/test_no_handbuilt_html.py` enforces this; ADR-0005).
- Every unsafe route is CSRF-protected by default (`tests/test_csrf.py` walks the url map). New POST routes need no special handling; do not add `@csrf.exempt`.
- Values interpolated into inline JS or `onsubmit` handlers go through `|tojson|forceescape` (ADR-0004).
- Admin invite tokens follow the portal-invite rule: returned once, stored only as a SHA-256 hash via `portal_auth.hash_token`, 7-day TTL (`portal_auth.INVITE_TTL`), minimum password length `portal_auth.MIN_PASSWORD_LENGTH` (12).
- Commit message style: imperative first line, no prefix (`Add`, `Drop`, `Move`), body only when the why is not obvious. End every commit message with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Do not touch the untracked `Invoice template creation.zip` or `design_handoff_dani_smith_design_system/` in the working tree.

---

## File Structure

**Create**
- `gigledger/team.py` — admin invites (`create_invite`, `open_invite`, `redeem_invite`, `cancel_invite`), admin removal rule (`can_remove`), first-run rule (`needs_setup`). Mirrors `portal_auth.py`'s shape so the two invite flows read alike.
- `gigledger/routes/team.py` — `team_bp`: `/settings/team/invite` (POST), `/settings/team/invites/<id>/cancel` (POST), `/settings/team/admins/<id>/remove` (POST), `/join/<token>` (GET/POST), `/setup` (GET/POST).
- `gigledger/templates/settings/_team.html` — the Team panel, included from `settings/index.html`.
- `gigledger/templates/auth/join.html` — redeem an admin invite.
- `gigledger/templates/auth/setup.html` — first-run setup.
- `tests/conftest.py` — `SEED_DEMO` autouse fixture, `build_app`, `app`, `admin_client`.
- `tests/test_business.py` — `Business` model, migration, context processor.
- `tests/test_team.py` — invites, join, remove, setup, signup gone, seed gate.
- `docs/adr/0014-one-business-per-install.md`.

**Modify**
- `gigledger/models.py` — add `Business`, `AdminInvite`; shrink `User`; move category helpers.
- `gigledger/app.py` — migration, seed gate, seed creates `Business`, context processor injects `business`, register `team_bp`.
- `gigledger/finance.py`, `gigledger/documents.py` — drop `user_id` parameters.
- `gigledger/routes/{auth,settings,dashboard,taxes,reports,transactions,inventory,recurring,goals,clients,projects,invoices,portal}.py` — drop scoping, read `Business.get()`.
- `gigledger/templates/base.html`, `settings/index.html`, `auth/login.html`, `invoices/export.html`, `invoices/detail.html`, `transactions/export.html`, `transactions/_edit_modal.html`, `transactions/index.html`, `dashboard/index.html`, `recurring/index.html`, `portal/index.html`.
- `gigledger/portal_auth.py` — module docstring and `visible_clients` docstring.
- `tests/test_documents.py`, `tests/test_document_sharing.py`, `tests/test_inventory.py`, `tests/test_portal_auth.py`, `tests/test_finance_characterization.py` — invert isolation cases; drop `user_id` arguments.
- `docs/glossary.md`, `README.md`.

**Delete**
- `gigledger/templates/auth/signup.html`.

---

### Task 1: `Business` model, migration, and the test scaffolding everything else needs

**Files:**
- Modify: `gigledger/models.py` (User at lines 84–147; add `Business` before `User`)
- Modify: `gigledger/app.py` (`_migrate_db` lines 195–283; `_seed_demo_data` lines 286–300)
- Create: `tests/conftest.py`
- Create: `tests/test_business.py`

**Interfaces:**
- Produces: `Business` model with columns `id, name, address, phone, default_tax_rate, currency, invoice_prefix, next_invoice_number, invoice_note, custom_income_categories, custom_expense_categories, custom_inventory_categories`; classmethod `Business.get() -> Business`; instance methods `get_income_categories()`, `get_expense_categories()`, `get_inventory_categories()`, `get_all_categories(kinds=KINDS)`, `get_next_invoice_number() -> str`.
- Produces: `User` with only `id, email, password_hash, theme, dark_mode, created_at, last_login_at`.
- Produces: test helpers in `tests/conftest.py`: `build_app(tmp_path, monkeypatch, **config)`, fixtures `app`, `admin_client`.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
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
```

Note: existing test modules define their own `build_app`/`app`; those keep working (a module-level fixture shadows the conftest one). Only new tests use these.

- [ ] **Step 2: Write the failing tests in `tests/test_business.py`**

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `venv/bin/python -m pytest tests/test_business.py -q`
Expected: ImportError — `cannot import name 'Business'`.

- [ ] **Step 4: Add `Business` to `gigledger/models.py`, shrink `User`**

Insert immediately before `class User(UserMixin, db.Model):` (currently line 84):

```python
class Business(db.Model):
    """The one business this install keeps books for.

    Exactly one row. Everything here used to be a column on `User`, back when
    one login was one business; now every admin shares these values, so they
    live where there is only one of them. `get()` is the single seam that
    fetches the row - no route or template asks *which* business. See
    docs/adr/0014.
    """
    __tablename__ = 'business'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), default='')
    address = db.Column(db.Text, default='')
    phone = db.Column(db.String(50), default='')
    default_tax_rate = db.Column(db.Float, default=0.30)
    currency = db.Column(db.String(3), default='USD')
    invoice_prefix = db.Column(db.String(10), default='INV')
    next_invoice_number = db.Column(db.Integer, default=1)
    invoice_note = db.Column(db.Text, default='Thank you for your business!')
    custom_income_categories = db.Column(db.Text, default='')     # comma-separated
    custom_expense_categories = db.Column(db.Text, default='')    # comma-separated
    custom_inventory_categories = db.Column(db.Text, default='')  # comma-separated

    @classmethod
    def get(cls):
        business = cls.query.first()
        if business is None:
            business = cls()
            db.session.add(business)
            db.session.commit()
        return business

    def get_income_categories(self):
        if self.custom_income_categories:
            return [c.strip() for c in self.custom_income_categories.split(',') if c.strip()]
        return DEFAULT_INCOME_CATEGORIES.copy()

    def get_expense_categories(self):
        if self.custom_expense_categories:
            return [c.strip() for c in self.custom_expense_categories.split(',') if c.strip()]
        return DEFAULT_EXPENSE_CATEGORIES.copy()

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

    def get_next_invoice_number(self):
        num = self.next_invoice_number
        self.next_invoice_number = num + 1
        db.session.commit()
        return f"{self.invoice_prefix}-{num:04d}"
```

Then replace the whole `User` class (from `class User(UserMixin, db.Model):` through the end of its `get_next_invoice_number` method) with:

```python
class User(UserMixin, db.Model):
    """An admin login. Personal preferences only: anything about the business
    is on `Business`, and every admin sees every row. `user_id` columns on
    other tables record who created a row and nothing more - no query reads
    them for visibility. See docs/adr/0014.
    """
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    theme = db.Column(db.String(20), default='emerald')  # theme name
    dark_mode = db.Column(db.Boolean, default=False)      # dark mode toggle
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    transactions = db.relationship('Transaction', backref='user', lazy=True)
    tax_estimates = db.relationship('TaxEstimate', backref='user', lazy=True)
    clients = db.relationship('Client', backref='user', lazy=True)
    invoices = db.relationship('Invoice', backref='user', lazy=True)
    projects = db.relationship('Project', backref='user', lazy=True)
    goals = db.relationship('Goal', backref='user', lazy=True)
    recurring_transactions = db.relationship('RecurringTransaction', backref='user', lazy=True)
```

- [ ] **Step 5: Migration and seed in `gigledger/app.py`**

In `_migrate_db`, replace the block that starts `# Check existing columns in users table` and ends with the `next_invoice_number` ALTER (lines 204–229) with:

```python
    # Users: business settings used to be columns here. They are left in place
    # on an existing database (SQLite cannot drop a column cleanly) and simply
    # stop being declared on the model. See docs/adr/0014.
    cursor.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cursor.fetchall()}
    if 'theme' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN theme VARCHAR(20) DEFAULT 'emerald'")
    if 'dark_mode' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN dark_mode BOOLEAN DEFAULT 0")
    if 'last_login_at' not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_login_at DATETIME")

    # Business: one row, seeded from the lowest-id user's old columns the first
    # time a pre-0014 database starts. create_all() has already made the table.
    cursor.execute("SELECT COUNT(*) FROM business")
    if cursor.fetchone()[0] == 0 and 'business_name' in user_columns:
        cursor.execute("""
            INSERT INTO business (name, address, phone, default_tax_rate, currency,
                                  invoice_prefix, next_invoice_number, invoice_note,
                                  custom_income_categories, custom_expense_categories,
                                  custom_inventory_categories)
            SELECT COALESCE(business_name, ''), COALESCE(business_address, ''),
                   COALESCE(business_phone, ''), COALESCE(default_tax_rate, 0.30),
                   COALESCE(currency, 'USD'), COALESCE(invoice_prefix, 'INV'),
                   COALESCE(next_invoice_number, 1),
                   COALESCE(invoice_note, 'Thank you for your business!'),
                   COALESCE(custom_income_categories, ''),
                   COALESCE(custom_expense_categories, ''),
                   COALESCE(custom_inventory_categories, '')
            FROM users ORDER BY id LIMIT 1
        """)
```

Note `_migrate_db` runs after `db.create_all()` in `create_app`, so `business` exists (empty) when the `SELECT COUNT(*)` runs. `custom_inventory_categories` may be absent from a very old `users` table: guard it — before the INSERT add

```python
    if 'custom_inventory_categories' not in user_columns and 'business_name' in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_inventory_categories TEXT DEFAULT ''")
```

In `_seed_demo_data`, replace the `demo_user = User(...)` construction (the seven-line call) with:

```python
    demo_user = User(email='demo@gigledger.com', password_hash=password_hash)
    db.session.add(demo_user)
    db.session.add(Business(
        name='Demo Freelance Studio',
        address='123 Creative Ave, San Francisco, CA 94102',
        phone='+1 (555) 123-4567',
        default_tax_rate=0.30, currency='USD',
        invoice_note='Payment due within 30 days. Thank you for your business!'))
    db.session.commit()
```

and remove the now-duplicate `db.session.add(demo_user)` / `db.session.commit()` that followed it. Add `Business` to the `from .models import (...)` at the top of `_seed_demo_data`. Further down, the seed reads `tax_rate = demo_user.default_tax_rate`; change to `tax_rate = Business.get().default_tax_rate`. After the invoice loop's `db.session.commit()`, add:

```python
    # The seed writes INV-0001..0008 by name; the counter must agree or the
    # first invoice created in the UI collides with INV-0001.
    Business.get().next_invoice_number = len(invoice_data) + 1
    db.session.commit()
```

- [ ] **Step 6: Run the new tests**

Run: `venv/bin/python -m pytest tests/test_business.py -q`
Expected: all PASS.

- [ ] **Step 7: Run the whole suite; expect the scoping-era failures, nothing else**

Run: `venv/bin/python -m pytest tests -q 2>&1 | tail -30`
Expected: failures only where routes/templates still read `current_user.default_tax_rate`, `current_user.currency`, `current_user.business_name`, `current_user.get_*_categories()` or `current_user.get_next_invoice_number()` (AttributeError). Any other failure is a regression in this task — fix it before committing. The follow-on tasks remove these AttributeErrors module by module; this commit is allowed to leave them.

- [ ] **Step 8: Commit**

```bash
git add gigledger/models.py gigledger/app.py tests/conftest.py tests/test_business.py
git commit -m "Add the Business row and migrate business settings off User

One install keeps books for one business, so the settings every admin
shares move to a single row. User keeps login and personal prefs only.
Routes still read the old attributes and are moved over next.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Inject `business` into templates; move Settings to the `Business` row

**Files:**
- Modify: `gigledger/app.py` (context processor `inject_port`, ~line 137)
- Modify: `gigledger/routes/settings.py` (whole file)
- Modify: `gigledger/templates/settings/index.html` (lines 31–70, 136–170)
- Modify: `gigledger/templates/base.html:648`
- Modify: `gigledger/templates/transactions/export.html:45`, `transactions/index.html:98`, `transactions/_edit_modal.html:82-84`, `dashboard/index.html:373-374`, `recurring/index.html` (the `current_user.get_*` / `current_user.currency` lines)
- Modify: `gigledger/templates/invoices/export.html:53-88`
- Test: `tests/test_business.py` (append)

**Interfaces:**
- Produces: template global `business` (a `Business` instance) available in every render.
- Consumes: `Business.get()` from Task 1.

- [ ] **Step 1: Write the failing tests (append to `tests/test_business.py`)**

```python
from tests.conftest import login_as


def test_every_template_sees_the_business(admin_client, app):
    with app.app_context():
        Business.get().name = 'Dani Smith Design'
        db.session.commit()
    body = admin_client.get('/settings').get_data(as_text=True)
    assert 'Dani Smith Design' in body


def test_settings_edit_the_shared_business_row(admin_client, app):
    admin_client.post('/settings/business', data={
        'business_name': 'Dani Smith Design', 'business_address': 'Denver, CO',
        'business_phone': '303', 'invoice_note': 'Thanks', 'invoice_prefix': 'DSD'})
    admin_client.post('/settings/tax-rate', data={'tax_rate': '25'})
    admin_client.post('/settings/currency', data={'currency': 'GBP'})
    admin_client.post('/settings/categories/income/add', data={'category_name': 'Design Fee'})

    with app.app_context():
        business = Business.get()
        assert business.name == 'Dani Smith Design'
        assert business.invoice_prefix == 'DSD'
        assert business.default_tax_rate == 0.25
        assert business.currency == 'GBP'
        assert 'Design Fee' in business.get_income_categories()


def test_a_second_admin_sees_the_same_business_settings(admin_client, app):
    admin_client.post('/settings/business', data={
        'business_name': 'Shared Name', 'business_address': '', 'business_phone': '',
        'invoice_note': '', 'invoice_prefix': 'INV'})
    with app.app_context():
        other = User(email='assistant@example.com', password_hash='x')
        db.session.add(other)
        db.session.commit()
        other_id = other.id

    body = login_as(app, other_id).get('/settings').get_data(as_text=True)
    assert 'Shared Name' in body


def test_theme_and_dark_mode_stay_personal(admin_client, app):
    admin_client.post('/settings/dark-mode', data={'dark_mode': 'on'})
    with app.app_context():
        other = User(email='assistant@example.com', password_hash='x')
        db.session.add(other)
        db.session.commit()
        assert User.query.get(1).dark_mode is True
        assert bool(other.dark_mode) is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `venv/bin/python -m pytest tests/test_business.py -q`
Expected: the four new tests FAIL (AttributeError on `current_user.default_tax_rate` in the settings route / template).

- [ ] **Step 3: Context processor in `gigledger/app.py`**

In `inject_port`, change the return to:

```python
        from .models import INVENTORY_CATEGORY_GUIDANCE, Business
        return {'xport': port, 'url_port': url_port, 'currency_symbols': CURRENCY_SYMBOLS,
                'inventory_guidance': INVENTORY_CATEGORY_GUIDANCE,
                'business': Business.get()}
```

- [ ] **Step 4: Rewrite `gigledger/routes/settings.py`**

Replace the file's contents with:

```python
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import (db, Business, DEFAULT_INCOME_CATEGORIES, DEFAULT_EXPENSE_CATEGORIES,
                      DEFAULT_INVENTORY_CATEGORIES)

settings_bp = Blueprint('settings', __name__)

AVAILABLE_THEMES = {
    'emerald': {'name': 'Emerald', 'desc': 'Fresh green, the default'},
    'ocean': {'name': 'Ocean Blue', 'desc': 'Deep and professional'},
    'sunset': {'name': 'Sunset Orange', 'desc': 'Warm and energetic'},
    'rose': {'name': 'Rose Pink', 'desc': 'Bold and vibrant'},
    'midnight': {'name': 'Midnight Slate', 'desc': 'Sleek and sophisticated'},
}


@settings_bp.route('/settings')
@login_required
def index():
    business = Business.get()
    return render_template('settings/index.html', user=current_user,
        tax_rate_percent=int(business.default_tax_rate * 100),
        income_categories=business.get_income_categories(),
        expense_categories=business.get_expense_categories(),
        default_income_categories=DEFAULT_INCOME_CATEGORIES,
        default_expense_categories=DEFAULT_EXPENSE_CATEGORIES,
        inventory_categories=business.get_inventory_categories(),
        default_inventory_categories=DEFAULT_INVENTORY_CATEGORIES,
        available_themes=AVAILABLE_THEMES,
        current_theme=current_user.theme or 'emerald',
        dark_mode=current_user.dark_mode or False)


@settings_bp.route('/settings/tax-rate', methods=['POST'])
@login_required
def update_tax_rate():
    try:
        tax_rate = float(request.form.get('tax_rate', '30'))
        if tax_rate < 0 or tax_rate > 100: raise ValueError
        Business.get().default_tax_rate = tax_rate / 100.0
        db.session.commit()
        flash(f'Tax rate updated to {tax_rate}%. All calculations now use this rate.', 'success')
    except ValueError:
        flash('Please enter a valid tax rate between 0 and 100.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/currency', methods=['POST'])
@login_required
def update_currency():
    currency = request.form.get('currency', 'USD')
    if currency in ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'INR', 'JPY']:
        Business.get().currency = currency
        db.session.commit()
        flash(f'Currency updated to {currency}.', 'success')
    else:
        flash('Unsupported currency.', 'error')
    return redirect(url_for('settings.index'))


# One list per kind. The routes stay separate endpoints - templates and the
# CSRF walk name them - but the behaviour is written once.
CATEGORY_LISTS = {
    'income': ('Income', 'custom_income_categories', DEFAULT_INCOME_CATEGORIES),
    'expense': ('Expense', 'custom_expense_categories', DEFAULT_EXPENSE_CATEGORIES),
    'inventory': ('Inventory', 'custom_inventory_categories', DEFAULT_INVENTORY_CATEGORIES),
}


def _current(kind):
    business = Business.get()
    return {'income': business.get_income_categories,
            'expense': business.get_expense_categories,
            'inventory': business.get_inventory_categories}[kind]()


def _add_category(kind):
    label, column, _ = CATEGORY_LISTS[kind]
    name = request.form.get('category_name', '').strip()
    if not name:
        flash('Category name cannot be empty.', 'error')
    elif name in _current(kind):
        flash(f'Category "{name}" already exists.', 'error')
    else:
        setattr(Business.get(), column, ','.join(_current(kind) + [name]))
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
        setattr(Business.get(), column, ','.join(cats) if cats else '')
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


@settings_bp.route('/settings/theme', methods=['POST'])
@login_required
def update_theme():
    theme = request.form.get('theme', 'emerald')
    if theme in AVAILABLE_THEMES:
        current_user.theme = theme
        db.session.commit()
        flash(f'Theme changed to {AVAILABLE_THEMES[theme]["name"]}.', 'success')
    else:
        flash('Invalid theme selection.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/dark-mode', methods=['POST'])
@login_required
def toggle_dark_mode():
    dark = request.form.get('dark_mode', 'off')
    current_user.dark_mode = (dark == 'on')
    db.session.commit()
    flash('Dark mode ' + ('enabled' if current_user.dark_mode else 'disabled') + '.', 'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/categories/reset', methods=['POST'])
@login_required
def reset_categories():
    business = Business.get()
    business.custom_income_categories = ''
    business.custom_expense_categories = ''
    business.custom_inventory_categories = ''
    db.session.commit()
    flash('Categories reset to defaults.', 'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/business', methods=['POST'])
@login_required
def update_business():
    business = Business.get()
    business.name = request.form.get('business_name', '').strip()
    business.address = request.form.get('business_address', '').strip()
    business.phone = request.form.get('business_phone', '').strip()
    business.invoice_note = request.form.get('invoice_note', '').strip()
    business.invoice_prefix = request.form.get('invoice_prefix', 'INV').strip()
    db.session.commit()
    flash('Business profile updated!', 'success')
    return redirect(url_for('settings.index'))
```

- [ ] **Step 5: `gigledger/templates/settings/index.html`**

Reorder and relabel the panels so the page reads Business → Team (placeholder for Task 8) → My account. Concretely:

1. Move the "Business Profile" card (lines 43–73) to the top of the grid, rename its heading to `Business`, and under the heading add
   `<p class="text-xs text-gray-400 mb-4">These settings are shared by every admin.</p>`.
   Inside it replace `user.business_name` → `business.name`, `user.business_address` → `business.address`, `user.business_phone` → `business.phone`, `user.invoice_prefix` → `business.invoice_prefix`, `user.invoice_note` → `business.invoice_note`. Keep the form field `name=` attributes unchanged (the route reads `business_name` etc.).
2. Move the "Tax Rate" and "Currency" cards directly after it (they are business settings). In the currency `<select>`, replace every `user.currency` with `business.currency`.
3. Keep the three category cards and "Reset Categories" after those.
4. Rename the "Profile" card heading (line 32) to `My account` and move it, "Dark Mode" and "Theme" to the end of the page. Its `user.email` / `user.created_at` references stay.

Leave a comment marker where the Team panel will go, right after the Business card:
`{# Team panel: settings/_team.html, included in Task 8 #}`

- [ ] **Step 6: The other templates**

Apply these exact replacements:

| File | Old | New |
|---|---|---|
| `base.html:648` | `current_user.default_tax_rate` | `business.default_tax_rate` |
| `transactions/export.html:45` | `current_user.default_tax_rate` | `business.default_tax_rate` |
| `transactions/index.html:98` | `current_user.default_tax_rate` | `business.default_tax_rate` |
| `transactions/_edit_modal.html:82-84` | `current_user.get_income_categories()` etc. (3 lines) | `business.get_income_categories()` etc. |
| `dashboard/index.html:373-374` | `current_user.get_income_categories()` / `get_expense_categories()` | `business.get_income_categories()` / `business.get_expense_categories()` |
| `recurring/index.html` | every `current_user.get_*_categories(` and `current_user.currency` | `business.get_*_categories(` / `business.currency` |
| `invoices/export.html:53-55` | `current_user.business_name`, `current_user.business_address`, `current_user.business_phone` | `business.name`, `business.address`, `business.phone` |
| `invoices/export.html:82` | `current_user.default_tax_rate` | `business.default_tax_rate` |
| `invoices/export.html:88` | `current_user.invoice_note` (both occurrences) | `business.invoice_note` |

Then verify nothing is left: `grep -rn "current_user\.\(business\|invoice_note\|invoice_prefix\|default_tax\|currency\|get_.*categories\|next_invoice\)" gigledger/templates` must print nothing.

- [ ] **Step 7: Run the tests**

Run: `venv/bin/python -m pytest tests/test_business.py tests/test_template_escaping.py tests/test_no_handbuilt_html.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add gigledger/app.py gigledger/routes/settings.py gigledger/templates tests/test_business.py
git commit -m "Read business settings from the Business row in Settings and templates

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Drop scoping from the finance helpers, dashboard, taxes, reports

**Files:**
- Modify: `gigledger/finance.py` (every function taking `user_id`)
- Modify: `gigledger/documents.py:135-157` (`per_project_stats`)
- Modify: `gigledger/routes/dashboard.py`, `gigledger/routes/taxes.py`, `gigledger/routes/reports.py`
- Modify: `tests/test_finance_characterization.py` (call sites at lines 74–112)

**Interfaces:**
- Produces: `finance._get_tx_range(start_date, end_date)`, `calculate_monthly_summary(year, month)`, `calculate_safe_to_spend(tax_rate)`, `calculate_quarterly_income_deductions(quarter, year)`, `calculate_quarterly_tax(quarter, year, tax_rate)`, `calculate_runway()`, `get_6_month_chart_data()`, `get_recent_transactions(limit=5)`, `get_category_breakdown(year=None, month=None)`; `documents.per_project_stats()`.

- [ ] **Step 1: Update the finance characterization test call sites**

In `tests/test_finance_characterization.py`, remove the first positional argument `demo_user_id(ledger)` from every call to `calculate_monthly_summary`, `calculate_quarterly_income_deductions`, `calculate_quarterly_tax`, `calculate_safe_to_spend`, `get_category_breakdown` (lines 74–112). Delete the `demo_user_id` helper if nothing else uses it (check with `grep -n demo_user_id tests/test_finance_characterization.py`).

- [ ] **Step 2: Run to see them fail**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py -q`
Expected: TypeError (missing positional argument).

- [ ] **Step 3: `gigledger/finance.py`**

Remove the `user_id` parameter from every function signature and every internal call, and delete each `Transaction.user_id == user_id,` filter line (there are four: in `_get_tx_range`, `calculate_safe_to_spend`, `calculate_runway`, `get_category_breakdown`) and the `filter_by(user_id=user_id)` in `get_recent_transactions` (becomes `Transaction.query`). Resulting signatures:

```python
def _get_tx_range(start_date, end_date):
def calculate_monthly_summary(year, month):
def calculate_safe_to_spend(tax_rate):
def calculate_quarterly_income_deductions(quarter, year):
def calculate_quarterly_tax(quarter, year, tax_rate):
def calculate_runway():
def get_6_month_chart_data():
def get_recent_transactions(limit=5):
def get_category_breakdown(year=None, month=None):
```

Add one line to the module docstring: `Every query here is business-wide: there is one business per install (ADR-0014).`

- [ ] **Step 4: `gigledger/documents.py` `per_project_stats`**

```python
def per_project_stats():
    """{project_id: (count, latest created_at, latest client-added created_at)}
    for every project.
    ...
```

Delete the `.filter_by(user_id=user_id)` line from its query.

- [ ] **Step 5: `routes/dashboard.py`**

Delete `uid = current_user.id`. Replace `tax_rate = current_user.default_tax_rate` with `business = Business.get()` / `tax_rate = business.default_tax_rate` (add `Business` to the models import). Drop the `uid` argument from every `calculate_*`, `get_*` and `_get_tx_range` call. Change:

```python
    goals = Goal.query.filter_by(is_completed=False).order_by(...)
    total_goals_saved = sum(g.current_amount for g in Goal.query.all())
    active_projects = Project.query.filter_by(status='active').order_by(...)
    active_project_count = Project.query.filter_by(status='active').count()
    unpaid_invoices = Invoice.query.filter(Invoice.status.in_(['sent', 'overdue'])).order_by(...)
    overdue_count = Invoice.query.filter_by(status='overdue').count()
    client_count = Client.query.filter_by(is_active=True).count()
    recurring_active = RecurringTransaction.query.filter_by(is_active=True, frequency='monthly').all()
```

and `currency=current_user.currency` → `currency=business.currency`, `user_categories=current_user.get_all_categories()` → `user_categories=business.get_all_categories()`.

- [ ] **Step 6: `routes/taxes.py`**

Both routes: delete `uid = current_user.id`; `tax_rate = Business.get().default_tax_rate`; drop `uid` from `calculate_quarterly_tax` / `calculate_safe_to_spend`; `TaxEstimate.query.filter_by(quarter=q, year=current_year)`; keep `TaxEstimate(user_id=current_user.id, ...)` on the write; `currency=Business.get().currency`.

- [ ] **Step 7: `routes/reports.py`**

Delete `uid = current_user.id`; `business = Business.get()`; `tax_rate = business.default_tax_rate`; remove every `Transaction.user_id == uid,`, `Client.user_id == uid,`, `Invoice.user_id == uid,` line (lines 28, 41, 90, 91, 101, 119, 143, 155, 156); drop `uid` from `calculate_monthly_summary(...)`; `currency=business.currency`.

- [ ] **Step 8: Run**

Run: `venv/bin/python -m pytest tests/test_finance_characterization.py tests/test_smoke.py -q`
Expected: PASS. Also `venv/bin/python -c "import gigledger.routes.dashboard, gigledger.routes.taxes, gigledger.routes.reports"` imports cleanly.

- [ ] **Step 9: Commit**

```bash
git add gigledger/finance.py gigledger/documents.py gigledger/routes/dashboard.py gigledger/routes/taxes.py gigledger/routes/reports.py tests/test_finance_characterization.py
git commit -m "Drop per-user scoping from finance, dashboard, taxes and reports

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Drop scoping from transactions, inventory, recurring, goals

**Files:**
- Modify: `gigledger/routes/transactions.py`, `gigledger/routes/inventory.py`, `gigledger/routes/recurring.py`, `gigledger/routes/goals.py`
- Modify: `tests/test_inventory.py:226-238` and `:478-495`

**Interfaces:**
- Consumes: `Business.get()`; `documents.per_project_stats()` (not used here, listed for completeness).
- Produces: `transactions._filtered_transactions(args)`, `transactions._inventory_fields(form)`.

- [ ] **Step 1: Invert the two inventory isolation tests**

Replace `test_another_users_project_is_refused` with:

```python
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
```

Replace `test_the_inventory_page_shows_only_the_current_users_items` with:

```python
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
    assert '$1,350.00' in page
```

- [ ] **Step 2: Run to see them fail**

Run: `venv/bin/python -m pytest tests/test_inventory.py -q -k "another_admin or every_admin"`
Expected: FAIL (the first still refuses; the second still hides).

- [ ] **Step 3: `routes/transactions.py`**

- `_filtered_transactions(uid, args)` → `_filtered_transactions(args)`; its first line becomes `transactions = Transaction.query\`.
- `_inventory_fields(form, uid)` → `_inventory_fields(form)`; the project check becomes `if not Project.query.filter_by(id=candidate).first():`.
- In `index()`: delete `uid = current_user.id`; add `business = Business.get()`; `categories = business.get_all_categories()`; pass `business.default_tax_rate`, `currency=business.currency`, `user_categories=business.get_all_categories()`; `projects=Project.query.order_by(Project.name).all()`; `_filtered_transactions(request.args)`.
- In `add()`: `_inventory_fields(request.form)`; the flash uses `business.default_tax_rate` / `business.currency` (fetch `business = Business.get()` at the top of the function). Keep `user_id=current_user.id` on both `Transaction(...)` and `InventoryItem(...)` constructions.
- In `edit()` and `delete()`: `tx = Transaction.query.filter_by(id=id).first()`; `_inventory_fields(request.form)`.
- In the two export routes: delete `uid = ...`, `_filtered_transactions(request.args)`, and read tax rate / currency from `Business.get()`.
- Add `Business` to the models import.

- [ ] **Step 4: `routes/inventory.py`**

Delete `uid = current_user.id`; `everything = (InventoryItem.query` (no `filter_by`); `categories=sorted(Business.get().get_inventory_categories())`; `projects=Project.query.order_by(Project.name).all()`; `currency=Business.get().currency`. Import `Business`.

- [ ] **Step 5: `routes/recurring.py`**

`RecurringTransaction.query.order_by(` (line 40); `currency=Business.get().currency`; `user_categories=Business.get().get_all_categories(kinds={INCOME, EXPENSE})`; lines 131/179/207 → `RecurringTransaction.query.filter_by(id=id).first()`; line 224 → `filter_by(is_active=True).all()`. Keep `user_id=current_user.id` on the two constructions (lines 111, 231). Import `Business`.

- [ ] **Step 6: `routes/goals.py`**

Line 30 → `Goal.query.order_by(...)`; `currency=Business.get().currency` (lines 39, 110); lines 90/119/162/176 → `Goal.query.filter_by(id=id).first()`. Keep `user_id=current_user.id` on the construction (line 74). Import `Business`.

- [ ] **Step 7: Run**

Run: `venv/bin/python -m pytest tests/test_inventory.py tests/test_transaction_kind.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add gigledger/routes/transactions.py gigledger/routes/inventory.py gigledger/routes/recurring.py gigledger/routes/goals.py tests/test_inventory.py
git commit -m "Drop per-user scoping from transactions, inventory, recurring and goals

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Drop scoping from clients, projects, invoices; invert the document tests

**Files:**
- Modify: `gigledger/routes/clients.py`, `gigledger/routes/projects.py`, `gigledger/routes/invoices.py`
- Modify: `gigledger/templates/invoices/detail.html:55-60,136-139`
- Modify: `tests/test_documents.py` (tests at lines 210, 311, 370, 438), `tests/test_document_sharing.py` (191, 208), `tests/test_portal_auth.py` (402)

**Interfaces:**
- Produces: `projects._owned_project(id)`, `projects._owned_document(doc_id)`, `clients._owned_client(id)` — same names, now primary-key lookups that 404 when absent.

- [ ] **Step 1: Invert the isolation tests**

`tests/test_documents.py`:

```python
def test_download_serves_a_document_uploaded_by_another_admin(app):
    """Every admin works the same books (ADR-0014)."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf', content=b'hello')

    with app.app_context():
        doc_id = ProjectDocument.query.one().id
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    response = login(app, colleague_id).get(f'/projects/documents/{doc_id}/download')

    assert response.status_code == 200
    assert response.data == b'hello'


def test_delete_removes_a_document_uploaded_by_another_admin(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        doc = ProjectDocument.query.one()
        doc_id, stored = doc.id, doc.stored_name
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    login(app, colleague_id).post(f'/projects/documents/{doc_id}/delete')

    with app.app_context():
        assert ProjectDocument.query.count() == 0
    assert not os.path.exists(gigledger.documents.path_for(stored))


def test_project_detail_is_visible_to_every_admin(app):
    pid = a_project(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    assert login(app, colleague_id).get(f'/projects/{pid}').status_code == 200


def test_documents_added_by_another_admin_count_on_every_admins_list(app):
    """The aggregate is business-wide (ADR-0014): a document one admin added
    shows on the card whoever is looking."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    row = documents_row(app, pid, user_id=colleague_id)

    assert 'No documents' not in row
    assert '1 document' in row
```

These four replace, in order, `test_download_refuses_another_users_document`,
`test_delete_refuses_another_users_document`,
`test_project_detail_refuses_another_users_project` and
`test_another_users_documents_do_not_count_on_my_list`. `documents_row` is the
existing helper at the bottom of the module (it extracts the card's documents
link and whatever sits beside it). Keep the original `test_delete_refuses_…`
body's exact assertions about bytes if they differ from the above.

`tests/test_document_sharing.py` — replace `test_a_document_cannot_be_shared_with_another_users_client` with:

```python
def test_a_document_can_be_shared_with_a_client_another_admin_added(app):
    doc_id = a_document(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        their_client = Client(user_id=colleague.id, name='Added By Colleague')
        db.session.add(their_client)
        db.session.commit()
        their_client_id = their_client.id

    share(app, doc_id, [their_client_id])

    with app.app_context():
        assert DocumentShare.query.filter_by(client_id=their_client_id).count() == 1
```

and `test_a_freelancer_cannot_share_another_users_document` with:

```python
def test_any_admin_can_share_a_document(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    freelancer(app, colleague_id).post(f'/projects/documents/{doc_id}/share',
                                       data={'client_ids': [str(client_id)]})

    with app.app_context():
        assert DocumentShare.query.count() == 1
```

`tests/test_portal_auth.py` — replace `test_a_freelancer_cannot_invite_another_users_client` with:

```python
def test_any_admin_can_invite_a_client(app):
    client_id = a_client_id(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = str(colleague_id)
        session['_fresh'] = True

    response = http.post(f'/clients/{client_id}/portal/invite',
                         data={'email': 'x@example.com'})

    assert response.status_code == 302
    with app.app_context():
        assert PortalInvite.query.filter_by(client_id=client_id).count() == 1
```

(`PortalInvite` and `User` are already imported at the top of that module.)

- [ ] **Step 2: Run to see them fail**

Run: `venv/bin/python -m pytest tests/test_documents.py tests/test_document_sharing.py tests/test_portal_auth.py -q -k "another_admin or every_admin or any_admin"`
Expected: FAIL.

- [ ] **Step 3: `routes/clients.py`**

- `list_clients`: delete `uid`; `clients = Client.query.order_by(Client.name.asc()).all()`; `currency=Business.get().currency`.
- Lines 55, 81, 95: `Client.query.filter_by(id=id).first()`.
- `_owned_client`: `client = db.session.get(Client, id)`; keep the 404.
- `currency=current_user.currency` (line 116) → `currency=Business.get().currency`.
- Keep `user_id=current_user.id` on the `Client(...)` construction (line 37). Import `Business`.

- [ ] **Step 4: `routes/projects.py`**

- `list_projects`: delete `uid`; `all_projects = Project.query.order_by(Project.created_at.desc()).all()`; `clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()`; `document_stats=documents.per_project_stats()`; `currency=Business.get().currency`.
- Lines 85 and 142: `owned = db.session.get(Client, int(client_id_raw))` (the variable name may stay; it still means "a real client id, else ignored").
- Lines 129, 178, 218, 241: `Project.query.filter_by(id=id).first()`.
- `_owned_project`: `project = db.session.get(Project, id)`; `_owned_document`: `doc = db.session.get(ProjectDocument, doc_id)`; both keep the 404. Update their docstrings/comments: "found or 404; every admin sees every project (ADR-0014)".
- Line 293: `clients=Client.query.filter_by(is_active=True)`; line 297: `currency=Business.get().currency`.
- Line 392 (`share_document`): `allowed = {c.id for c in Client.query.all()}`. Keep the comment's spirit — ids that are not real clients are dropped, not honoured.
- Keep every `user_id=current_user.id` on constructions and on `documents.record_access(doc, user_id=current_user.id)`. Import `Business`.

- [ ] **Step 5: `routes/invoices.py`**

- `list_invoices`: delete `uid`; `query = Invoice.query`; the three summaries use `Invoice.query.filter(...)` / `filter_by(status='paid')` / `filter_by(status='overdue')` with no user clause; `clients = Client.query.filter_by(is_active=True)...`; `currency=Business.get().currency`.
- `create` GET (line 48–58): same for clients; `tax_rate=business.default_tax_rate`, `currency=business.currency` with `business = Business.get()`.
- `create` POST: `tax_amount = subtotal * business.default_tax_rate`; `client_obj = db.session.get(Client, int(client_id))`; `invoice_number = business.get_next_invoice_number()`; keep `user_id=current_user.id` on `Invoice(...)`.
- Lines 167, 237, 257, 279: `Invoice.query.filter_by(id=id).first()`.
- Line 206 and 227: read tax rate / currency from `business`.
- `detail` (lines 257–273): pass `business_name=business.name, business_address=business.address, business_phone=business.phone, invoice_note=business.invoice_note` — or simpler, drop those four kwargs and let the template read the injected `business`. Do the simpler thing, and in `templates/invoices/detail.html` replace `business_name` → `business.name`, `business_address` → `business.address`, `business_phone` → `business.phone`, `invoice_note` → `business.invoice_note` at lines 55–60 and 136–139.
- Keep `user_id=current_user.id` on the payment transactions (lines 186, 201). Import `Business`.

- [ ] **Step 6: Run the whole suite**

Run: `venv/bin/python -m pytest tests -q`
Expected: everything passes except `test_document_sharing.py::test_documents_from_two_freelancers_are_grouped_by_who_shared_them` (or whatever the grouping test at ~line 258 is named) — that one is Task 6's. Confirm with `grep -rn "current_user\.\(business\|invoice_note\|invoice_prefix\|default_tax\|currency\|get_.*categories\|next_invoice\)\|user_id=current_user.id)\|user_id=uid\|user_id == uid" gigledger/routes gigledger/*.py` → only construction sites (`Model(user_id=current_user.id, ...)`) and `record_access(..., user_id=...)` should remain; no `filter_by(... user_id=` anywhere.

- [ ] **Step 7: Commit**

```bash
git add gigledger/routes/clients.py gigledger/routes/projects.py gigledger/routes/invoices.py gigledger/templates/invoices/detail.html tests/test_documents.py tests/test_document_sharing.py tests/test_portal_auth.py
git commit -m "Drop per-user scoping from clients, projects and invoices

@login_required is now the whole access rule on the admin side. The
user_id columns stay as a record of who created a row.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: The portal shows one business

**Files:**
- Modify: `gigledger/routes/portal.py:91-133` (`index`)
- Modify: `gigledger/templates/portal/index.html`
- Modify: `gigledger/portal_auth.py` (module docstring lines 1–12; `visible_clients` docstring lines 208–213)
- Modify: `tests/test_document_sharing.py` (`test_documents_from_two_freelancers_are_labelled_separately`, line 260)

**Interfaces:**
- Produces: template context for `portal/index.html`: `account`, `business_name: str`, `projects: list[dict(id, name, docs, can_add)]`, `new_ids`, `new_count`, `max_upload_mb` (the last three unchanged from today).

- [ ] **Step 1: Replace the grouping test**

Replace `test_documents_from_two_freelancers_are_labelled_separately` (line 260, docstring "PortalAccount is global, so one page can carry two tenants' material…") with:

```python
def test_the_portal_names_the_business_once_and_groups_by_project(app):
    """One business per install (ADR-0014): the heading is the business name,
    then projects, then documents. Nothing is grouped by who uploaded it."""
    first_client = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [first_client])
    http = portal_for(app, first_client, email='shared@example.com')

    with app.app_context():
        Business.get().name = 'Dani Smith Design'
        db.session.commit()
        other_project = Project(user_id=1, client_id=first_client, name='Other Project')
        db.session.add(other_project)
        db.session.commit()
        other_doc = ProjectDocument(user_id=1, project_id=other_project.id,
                                    kind='link', title='Other Brief',
                                    external_url='https://example.com/brief',
                                    provider='other')
        db.session.add(other_doc)
        db.session.commit()
        db.session.add(DocumentShare(document_id=other_doc.id, client_id=first_client))
        db.session.commit()

    body = http.get('/portal/').get_data(as_text=True)

    # One <h2> heading. The name also appears inside each project's upload
    # modal copy ("… will see it straight away"), so count the heading markup.
    assert body.count('<h2 class="text-sm font-bold text-gray-700">Dani Smith Design</h2>') == 1
    assert 'Signed contract' in body
    assert 'Other Brief' in body
    assert 'Other Project' in body
```

Add `Business` to the test module's `from gigledger.models import (...)`.

- [ ] **Step 2: Run to see it fail**

Run: `venv/bin/python -m pytest tests/test_document_sharing.py -q -k names_the_business`
Expected: FAIL (the heading still reads `Demo Freelance Studio`).

- [ ] **Step 3: `routes/portal.py` `index`**

Replace from the comment `# Every project the account is the client of is listed…` through the `return render_template(...)` with:

```python
    # Every project the account is the client of is listed, documents or not:
    # a client cannot add to a project they cannot see. A project reached only
    # through a share grant is listed too, but nothing can be added to it -
    # the grant gave access to one document, not to the project (ADR-0009).
    # One business per install (ADR-0014): the page is headed once with its
    # name and grouped by project.
    projects = {}

    def project_entry(project, can_add):
        entry = projects.get(project.id)
        if entry is None:
            entry = {'id': project.id, 'name': project.name, 'docs': [], 'can_add': can_add}
            projects[project.id] = entry
        return entry

    for project in documents_module.projects_of(clients):
        project_entry(project, can_add=True)
    for doc in documents:
        project_entry(db.session.get(Project, doc.project_id), can_add=False)['docs'].append(doc)

    return render_template('portal/index.html',
                           account=g.portal_account,
                           business_name=Business.get().name,
                           projects=list(projects.values()),
                           new_ids=new_ids,
                           new_count=len(new_ids),
                           max_upload_mb=documents_module.MAX_UPLOAD_BYTES // (1024 * 1024))
```

Add `Business` to the `from ..models import (...)`; remove `User` from it if `grep -n "User" gigledger/routes/portal.py` shows no other use. Leave the ADR-0012 block above (`new_ids` / `mark_documents_seen`) exactly as it is.

- [ ] **Step 4: `templates/portal/index.html`**

Three edits, nothing else:

1. Header comment: replace the first paragraph ("Grouped by the business the account is a client of … See ADR-0008.") with:
   `Headed by the business name, then grouped by project. One install keeps books for one business (ADR-0014), so there is no owner to group by.`
2. Replace the two lines
   ```
   {% if groups %}
       {% for group in groups %}
   ```
   with
   ```
   {% if projects %}
   ```
   and `<h2 class="text-sm font-bold text-gray-700">{{ group.name }}</h2>` with `<h2 class="text-sm font-bold text-gray-700">{{ business_name }}</h2>`, and `{% for project in group.projects %}` with `{% for project in projects %}`. At the bottom, the closing pair
   ```
       </section>
       {% endfor %}
   ```
   becomes just `    </section>` (one `{% endfor %}` removed — the project loop's `{% endfor %}` above it stays).
3. In the two modals, replace all three occurrences of `{{ group.name }}` with `{{ business_name }}`.

Everything else in the file — the "new" banner and pill (ADR-0012), the Upload/Add link buttons and modals (ADR-0013), the "No documents yet." fallback — stays byte-for-byte.

- [ ] **Step 5: `portal_auth.py` docstrings**

Module docstring: replace the sentence(s) about `~40 routes filter rows with user_id=current_user.id` with:

> `current_user` means *admin* everywhere in this app, and an admin sees every row (ADR-0014). If a client could become `current_user`, a portal account would be an admin. A Portal Session and an admin session are therefore mutually exclusive, so no request has two principals.

`visible_clients` docstring: replace "PortalAccount is the schema's one cross-tenant object; this function is the seam where a request drops back into tenant-scoped data" with "Every portal query starts here: this function is the seam between a login and the Client rows it may act through, and nothing in the portal should reach around it."

- [ ] **Step 6: Run**

Run: `venv/bin/python -m pytest tests/test_document_sharing.py tests/test_portal_auth.py tests/test_template_escaping.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gigledger/routes/portal.py gigledger/templates/portal/index.html gigledger/portal_auth.py tests/test_document_sharing.py
git commit -m "Head the portal with the one business name and group by project

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Remove sign-up, gate the seed, add first-run setup

**Files:**
- Modify: `gigledger/routes/auth.py` (delete `signup`; record `last_login_at`; redirect to setup)
- Delete: `gigledger/templates/auth/signup.html`
- Modify: `gigledger/templates/auth/login.html:193`
- Modify: `gigledger/app.py` (seed gate; register `team_bp`)
- Create: `gigledger/team.py` (`needs_setup` only in this task; invites in Task 8)
- Create: `gigledger/routes/team.py` (`/setup` only in this task)
- Create: `gigledger/templates/auth/setup.html`
- Create: `tests/test_team.py`

**Interfaces:**
- Produces: `team.needs_setup() -> bool`; blueprint `team_bp` with endpoint `team.setup`.

- [ ] **Step 1: Write the failing tests in `tests/test_team.py`**

```python
"""Admins: how they get in, how they are added, how they are removed.

Spec: docs/superpowers/specs/2026-09-15-one-business-per-install-design.md
"""
import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import Business, User, db
from tests.conftest import build_app, login_as


@pytest.fixture
def empty_app(tmp_path, monkeypatch):
    """A fresh install: no SEED_DEMO, so no users and no business."""
    monkeypatch.delenv('SEED_DEMO', raising=False)
    return build_app(tmp_path, monkeypatch)


# --- sign-up is gone, the seed is opt-in ---------------------------------

def test_signup_no_longer_exists(app):
    assert app.test_client().get('/signup').status_code == 404
    assert 'auth.signup' not in {r.endpoint for r in app.url_map.iter_rules()}


def test_the_login_page_does_not_link_to_signup(app):
    body = app.test_client().get('/login').get_data(as_text=True)
    assert 'Sign up' not in body
    assert '/signup' not in body


def test_the_demo_seed_does_not_run_without_the_flag(empty_app):
    with empty_app.app_context():
        assert User.query.count() == 0
        assert Business.query.count() == 0


def test_the_demo_seed_runs_with_the_flag(app):
    with app.app_context():
        assert User.query.filter_by(email='demo@gigledger.com').count() == 1


# --- first run -----------------------------------------------------------

def test_login_redirects_to_setup_while_there_are_no_admins(empty_app):
    response = empty_app.test_client().get('/login')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/setup')


def test_setup_creates_the_business_and_the_first_admin(empty_app):
    http = empty_app.test_client()
    response = http.post('/setup', data={
        'business_name': 'Dani Smith Design',
        'email': 'dani@example.com',
        'password': 'correct-horse-battery',
        'confirm_password': 'correct-horse-battery'})

    assert response.status_code == 302
    with empty_app.app_context():
        assert Business.get().name == 'Dani Smith Design'
        user = User.query.one()
        assert user.email == 'dani@example.com'
    # Signed in: the dashboard renders rather than bouncing to /login.
    assert http.get('/').status_code == 200


def test_setup_rejects_a_short_password(empty_app):
    empty_app.test_client().post('/setup', data={
        'business_name': 'X', 'email': 'dani@example.com',
        'password': 'short', 'confirm_password': 'short'})
    with empty_app.app_context():
        assert User.query.count() == 0


def test_setup_is_a_404_once_an_admin_exists(app):
    assert app.test_client().get('/setup').status_code == 404
    response = app.test_client().post('/setup', data={
        'business_name': 'X', 'email': 'intruder@example.com',
        'password': 'correct-horse-battery', 'confirm_password': 'correct-horse-battery'})
    assert response.status_code == 404
    with app.app_context():
        assert User.query.filter_by(email='intruder@example.com').count() == 0


def test_login_records_last_login_at(app):
    from gigledger.app import bcrypt
    with app.app_context():
        user = User.query.get(1)
        user.password_hash = bcrypt.generate_password_hash('demo1234').decode('utf-8')
        db.session.commit()
    app.test_client().post('/login', data={'email': 'demo@gigledger.com',
                                           'password': 'demo1234'})
    with app.app_context():
        assert User.query.get(1).last_login_at is not None
```

- [ ] **Step 2: Run to see them fail**

Run: `venv/bin/python -m pytest tests/test_team.py -q`
Expected: FAIL — `/signup` is 200, seed runs regardless, `/setup` is 404 everywhere, `last_login_at` stays None.

- [ ] **Step 3: `gigledger/team.py` (first-run rule)**

```python
"""
GigLedger - Admin accounts: who may be one, and how they get in.

One install keeps books for one business, and every admin sees every row
(docs/adr/0014). Admins are therefore added by invitation from Settings rather
than by public sign-up, and a fresh install creates its first admin through a
setup screen that exists only while there are no admins at all.

The invite mechanics deliberately mirror portal_auth: the token is returned
exactly once and stored only hashed.
"""
from .models import User


def needs_setup():
    """True only while the install has no admin at all. The setup route uses
    this to exist, and the login route to redirect; it must never be true on
    a working install, which is what makes /setup safe to leave registered."""
    return User.query.count() == 0
```

- [ ] **Step 4: `gigledger/routes/team.py` (setup route)**

```python
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_user
from .. import team, portal_auth
from ..models import Business, User, db
from ..app import bcrypt

team_bp = Blueprint('team', __name__)


@team_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """First run only. 404 - not a refusal - once any admin exists, so the
    route cannot later be used to add an account (ADR-0014)."""
    if not team.needs_setup():
        abort(404)

    if request.method == 'POST':
        business_name = request.form.get('business_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not business_name or not email:
            flash('A business name and an email address are needed.', 'error')
        elif password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
        elif len(password) < portal_auth.MIN_PASSWORD_LENGTH:
            flash(f'Choose a password of at least {portal_auth.MIN_PASSWORD_LENGTH} characters.', 'error')
        else:
            business = Business.get()
            business.name = business_name
            user = User(email=email,
                        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'))
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash(f'Welcome to {business_name}.', 'success')
            return redirect(url_for('dashboard.index'))

    return render_template('auth/setup.html')
```

- [ ] **Step 5: `templates/auth/setup.html`**

Base it on `auth/login.html` (same wrapper markup, logo, card). Title block: `Set up - GigLedger`. Card heading "Set up your business"; sub-line "This creates the first admin account. More admins can be invited from Settings." Form `method="POST" action="{{ url_port('team.setup') }}"` with `{{ csrf_field() }}` and fields: `business_name` (text, required), `email` (email, required), `password` (password, required, `minlength="12"`), `confirm_password` (password, required); submit "Create account →". No sign-in link, no demo credentials block.

- [ ] **Step 6: `routes/auth.py`**

- Delete the whole `signup` view.
- At the top of `login()`, before the authenticated check:

```python
    if team.needs_setup():
        return redirect(url_for('team.setup'))
```

  and add `from .. import team` to the imports.
- On successful login, after `login_user(user, remember=True)`:

```python
            user.last_login_at = datetime.utcnow()
            db.session.commit()
```

  with `from datetime import datetime` added.

Delete `gigledger/templates/auth/signup.html`. In `auth/login.html` delete line 193 (the "Don't have an account? Sign up" paragraph).

- [ ] **Step 7: `gigledger/app.py`**

Register the blueprint alongside the others:

```python
    from .routes.team import team_bp
    ...
    app.register_blueprint(team_bp)
```

Gate the seed:

```python
    with app.app_context():
        db.create_all()
        _migrate_db(db)
        # Opt-in. The seed creates an admin with a public password, and on a
        # one-business install that admin sees everything (ADR-0014). A fresh
        # database without the flag starts at /setup instead.
        if os.environ.get('SEED_DEMO', '').lower() in ('1', 'true', 'yes'):
            _seed_demo_data()
```

- [ ] **Step 8: Run**

Run: `venv/bin/python -m pytest tests/test_team.py tests/test_csrf.py tests/test_no_handbuilt_html.py tests/test_template_escaping.py -q`
Expected: PASS. (`test_csrf` probes `POST /setup` without a token and expects a 400 from CSRF, which fires before the view — satisfied.)

- [ ] **Step 9: Update `autostart.env` note and the run wrapper**

The live service starts via `autostart.env` → `run.py` and the database is already seeded, so nothing changes for it. Add to `README.md` under the Client Portal section a line: `SEED_DEMO=1` seeds the demo business and `demo@gigledger.com` on an empty database; without it a fresh install starts at `/setup`. (Full README pass is Task 9; this one line goes in now so the flag is documented in the same commit that introduces it.)

- [ ] **Step 10: Commit**

```bash
git add gigledger/team.py gigledger/routes/team.py gigledger/routes/auth.py gigledger/app.py gigledger/templates/auth tests/test_team.py README.md
git rm gigledger/templates/auth/signup.html
git commit -m "Replace sign-up with first-run setup and make the demo seed opt-in

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Admin invites, join, and the Team panel

**Files:**
- Modify: `gigledger/models.py` (add `AdminInvite` after `PortalInvite`)
- Modify: `gigledger/team.py` (invites, removal rule)
- Modify: `gigledger/routes/team.py` (invite, cancel, remove, join)
- Modify: `gigledger/routes/settings.py` `index` (pass admins + invites)
- Create: `gigledger/templates/settings/_team.html`
- Modify: `gigledger/templates/settings/index.html` (include the panel at the marker)
- Create: `gigledger/templates/auth/join.html`
- Test: `tests/test_team.py` (append)

**Interfaces:**
- Produces: `AdminInvite` model (`id, email, token_hash, invited_by, expires_at, redeemed_at, created_at`, `is_open()`); `team.create_invite(email, invited_by) -> (AdminInvite, token) | raises ValueError`; `team.open_invite(token) -> AdminInvite | None`; `team.redeem_invite(invite, password) -> (User, None) | (None, error_str)`; `team.cancel_invite(invite)`; `team.can_remove(target, actor) -> (bool, reason_str)`.
- Endpoints: `team.invite` (POST `/settings/team/invite`), `team.cancel_invite` (POST `/settings/team/invites/<int:id>/cancel`), `team.remove_admin` (POST `/settings/team/admins/<int:id>/remove`), `team.join` (GET/POST `/join/<token>`).

- [ ] **Step 1: Write the failing tests (append to `tests/test_team.py`)**

```python
from datetime import datetime, timedelta

from gigledger.models import AdminInvite
from gigledger import team


def _invite(admin_client, app, email='assistant@example.com'):
    """Issue an invite through the UI and return the join URL it showed once."""
    admin_client.post('/settings/team/invite', data={'email': email})
    body = admin_client.get('/settings').get_data(as_text=True)
    start = body.index('/join/')
    end = body.index('"', start)
    return body[start:end]


# --- invites -------------------------------------------------------------

def test_an_invite_is_shown_once_and_stored_hashed(admin_client, app):
    url = _invite(admin_client, app)
    token = url.rsplit('/', 1)[1]
    with app.app_context():
        invite = AdminInvite.query.one()
        assert invite.email == 'assistant@example.com'
        assert invite.token_hash != token
        assert invite.invited_by == 1
        assert invite.is_open()
    # A second look at the page no longer carries the link.
    assert '/join/' not in admin_client.get('/settings').get_data(as_text=True)


def test_an_invite_for_an_existing_admin_is_refused(admin_client, app):
    admin_client.post('/settings/team/invite', data={'email': 'demo@gigledger.com'})
    with app.app_context():
        assert AdminInvite.query.count() == 0


def test_reissuing_closes_the_previous_invite(admin_client, app):
    first = _invite(admin_client, app)
    _invite(admin_client, app)
    with app.app_context():
        assert AdminInvite.query.count() == 2
        assert sum(1 for i in AdminInvite.query.all() if i.is_open()) == 1
    assert app.test_client().get(first).status_code == 302  # no longer valid


def test_an_open_invite_can_be_cancelled(admin_client, app):
    _invite(admin_client, app)
    with app.app_context():
        invite_id = AdminInvite.query.one().id
    admin_client.post(f'/settings/team/invites/{invite_id}/cancel')
    with app.app_context():
        assert not AdminInvite.query.get(invite_id).is_open()


# --- join ----------------------------------------------------------------

def test_joining_creates_the_admin_and_signs_them_in(admin_client, app):
    url = _invite(admin_client, app)
    http = app.test_client()

    page = http.get(url).get_data(as_text=True)
    assert 'Demo Freelance Studio' in page

    response = http.post(url, data={'password': 'correct-horse-battery',
                                    'confirm_password': 'correct-horse-battery'})

    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(email='assistant@example.com').one()
        assert AdminInvite.query.one().redeemed_at is not None
    assert http.get('/').status_code == 200
    # And they see the shared books.
    assert 'Website Redesign' in http.get('/projects/').get_data(as_text=True)


def test_a_short_password_does_not_join(admin_client, app):
    url = _invite(admin_client, app)
    app.test_client().post(url, data={'password': 'short', 'confirm_password': 'short'})
    with app.app_context():
        assert User.query.filter_by(email='assistant@example.com').count() == 0


@pytest.mark.parametrize('spoil', ['expired', 'redeemed', 'unknown'])
def test_a_spoiled_invite_gets_one_and_the_same_answer(admin_client, app, spoil):
    url = _invite(admin_client, app)
    with app.app_context():
        invite = AdminInvite.query.one()
        if spoil == 'expired':
            invite.expires_at = datetime.utcnow() - timedelta(seconds=1)
        elif spoil == 'redeemed':
            invite.redeemed_at = datetime.utcnow()
        db.session.commit()
    if spoil == 'unknown':
        url = '/join/not-a-real-token'

    http = app.test_client()
    get = http.get(url)
    post = http.post(url, data={'password': 'correct-horse-battery',
                                'confirm_password': 'correct-horse-battery'})

    assert get.status_code == 302 and get.headers['Location'].endswith('/login')
    assert post.status_code == 302 and post.headers['Location'].endswith('/login')
    with app.app_context():
        assert User.query.filter_by(email='assistant@example.com').count() == 0


# --- removing admins -----------------------------------------------------

def _second_admin(app):
    with app.app_context():
        other = User(email='assistant@example.com', password_hash='x')
        db.session.add(other)
        db.session.commit()
        return other.id


def test_an_admin_can_be_removed_and_their_rows_stay(admin_client, app):
    other_id = _second_admin(app)
    from gigledger.models import Client
    with app.app_context():
        db.session.add(Client(user_id=other_id, name='Added By Assistant'))
        db.session.commit()

    admin_client.post(f'/settings/team/admins/{other_id}/remove')

    with app.app_context():
        assert User.query.get(other_id) is None
        assert Client.query.filter_by(name='Added By Assistant').count() == 1


def test_you_cannot_remove_yourself(admin_client, app):
    _second_admin(app)
    admin_client.post('/settings/team/admins/1/remove')
    with app.app_context():
        assert User.query.get(1) is not None


def test_the_last_admin_cannot_be_removed(admin_client, app):
    other_id = _second_admin(app)
    login_as(app, other_id).post('/settings/team/admins/1/remove')
    with app.app_context():
        assert User.query.count() == 1
    # Now the survivor is the last admin: nobody can remove them.
    login_as(app, other_id).post(f'/settings/team/admins/{other_id}/remove')
    with app.app_context():
        assert User.query.count() == 1


def test_the_team_panel_lists_admins_and_open_invites(admin_client, app):
    _second_admin(app)
    _invite(admin_client, app, email='new@example.com')
    body = admin_client.get('/settings').get_data(as_text=True)
    assert 'demo@gigledger.com' in body
    assert 'assistant@example.com' in body
    assert 'new@example.com' in body
```

- [ ] **Step 2: Run to see them fail**

Run: `venv/bin/python -m pytest tests/test_team.py -q`
Expected: the new tests FAIL (ImportError on `AdminInvite`).

- [ ] **Step 3: `AdminInvite` in `gigledger/models.py`** (after `PortalInvite`)

```python
class AdminInvite(db.Model):
    """A single-use, expiring grant that lets someone become an admin.

    Same rules as PortalInvite, for the same reason: the token is a bearer
    credential that will be pasted into email and chat, and a database read
    must not hand over working invites. See docs/adr/0014.
    """
    __tablename__ = 'admin_invites'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, index=True)
    invited_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_open(self):
        return self.redeemed_at is None and self.expires_at > datetime.utcnow()
```

`db.create_all()` creates the table on existing databases too, so no `_migrate_db` change is needed.

- [ ] **Step 4: `gigledger/team.py` (invites and removal)**

Append:

```python
import secrets
from datetime import datetime

from . import portal_auth
from .app import bcrypt
from .models import AdminInvite, User, db


def create_invite(email, invited_by):
    """Open an invite. Returns (invite, token); the token is returned exactly
    once, here. Raises ValueError if the email already belongs to an admin -
    refused at creation so the inviter sees it, not the invitee at redeem.

    Reissuing closes any invite still open for the same email: two live
    invites would mean cancelling one does not cancel access.
    """
    email = (email or '').strip().lower()
    if not email:
        raise ValueError('An email address is needed.')
    if User.query.filter_by(email=email).first():
        raise ValueError(f'{email} is already an admin.')

    for previous in AdminInvite.query.filter_by(email=email).all():
        if previous.is_open():
            previous.expires_at = datetime.utcnow()

    token = secrets.token_urlsafe(32)
    invite = AdminInvite(email=email, invited_by=invited_by,
                         token_hash=portal_auth.hash_token(token),
                         expires_at=datetime.utcnow() + portal_auth.INVITE_TTL)
    db.session.add(invite)
    return invite, token


def open_invite(token):
    invite = AdminInvite.query.filter_by(token_hash=portal_auth.hash_token(token)).first()
    return invite if invite and invite.is_open() else None


def cancel_invite(invite):
    invite.expires_at = datetime.utcnow()


def redeem_invite(invite, password):
    """Turn an open invite into an admin. Returns (user, error)."""
    if len(password or '') < portal_auth.MIN_PASSWORD_LENGTH:
        return None, f'Choose a password of at least {portal_auth.MIN_PASSWORD_LENGTH} characters.'
    if User.query.filter_by(email=invite.email).first():
        # Became an admin some other way since the invite was issued.
        return None, 'That invitation is no longer valid.'
    user = User(email=invite.email,
                password_hash=bcrypt.generate_password_hash(password).decode('utf-8'))
    db.session.add(user)
    invite.redeemed_at = datetime.utcnow()
    return user, None


def can_remove(target, actor):
    """(allowed, reason). Two refusals: yourself, and the last admin standing -
    either would lock the install."""
    if target.id == actor.id:
        return False, 'You cannot remove your own account.'
    if User.query.count() <= 1:
        return False, 'The last admin cannot be removed.'
    return True, ''
```

Note the circular-import shape: `team.py` imports `bcrypt` from `app.py`, exactly as `routes/auth.py` does, so it must be imported *inside* `create_app` consumers (the route module), not at `app.py` import time. `routes/team.py` already imports it that way.

- [ ] **Step 5: `gigledger/routes/team.py` (append the four routes)**

```python
from flask import session
from flask_login import login_required, current_user
from ..models import AdminInvite


@team_bp.route('/settings/team/invite', methods=['POST'])
@login_required
def invite():
    try:
        _, token = team.create_invite(request.form.get('email', ''), current_user.id)
    except ValueError as error:
        flash(str(error), 'error')
        return redirect(url_for('settings.index'))
    db.session.commit()

    # Handed back through the session for one render, the same way the client
    # invite is: the token exists nowhere else in a readable form, and the
    # session keeps it out of the URL, the history and the access log.
    session['admin_invite_url'] = url_for('team.join', token=token, _external=True)
    flash('Invitation created. Copy the link below and send it yourself.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/settings/team/invites/<int:id>/cancel', methods=['POST'])
@login_required
def cancel_invite(id):
    invite = db.session.get(AdminInvite, id)
    if invite and invite.is_open():
        team.cancel_invite(invite)
        db.session.commit()
        flash('Invitation cancelled.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/settings/team/admins/<int:id>/remove', methods=['POST'])
@login_required
def remove_admin(id):
    target = db.session.get(User, id)
    if not target:
        abort(404)
    allowed, reason = team.can_remove(target, current_user)
    if not allowed:
        flash(reason, 'error')
        return redirect(url_for('settings.index'))
    # The row goes; everything they created stays (its user_id now points at
    # nobody, which no query reads). No cascade, on purpose - ADR-0014.
    db.session.delete(target)
    db.session.commit()
    flash(f'{target.email} is no longer an admin.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/join/<token>', methods=['GET', 'POST'])
def join(token):
    invite = team.open_invite(token)
    if not invite:
        # One message for expired, already-used and never-existed alike.
        flash('That invitation link is no longer valid. Ask for a new one.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
            return render_template('auth/join.html', invite=invite, token=token)

        user, error = team.redeem_invite(invite, password)
        if error:
            db.session.rollback()
            flash(error, 'error')
            return render_template('auth/join.html', invite=invite, token=token)

        db.session.commit()
        portal_auth.forget_portal_session()
        login_user(user, remember=True)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        flash(f'Welcome to {Business.get().name}.', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('auth/join.html', invite=invite, token=token)
```

Add `from datetime import datetime` at the top of the module and merge the imports with the ones from Task 7.

- [ ] **Step 6: Settings `index` passes the team data**

In `routes/settings.py` `index()`, add to the `render_template` kwargs:

```python
        admins=User.query.order_by(User.created_at).all(),
        open_invites=[i for i in AdminInvite.query.order_by(AdminInvite.created_at).all()
                      if i.is_open()],
        invite_url=session.pop('admin_invite_url', None),
```

with `from flask import session` and `AdminInvite, User` added to the models import.

- [ ] **Step 7: `templates/settings/_team.html`**

```html
<div class="glass-card p-6 fade-up fade-up-delay-1">
    <h2 class="text-lg font-extrabold text-gray-900 mb-1">Team</h2>
    <p class="text-xs text-gray-400 mb-4">Every admin sees and edits the same books. Invite someone by creating a link and sending it yourself &mdash; GigLedger does not send email.</p>

    <div class="space-y-2 mb-5">
        {% for admin in admins %}
        <div class="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/60 border border-white/70">
            <div class="min-w-0">
                <p class="text-sm font-semibold text-gray-800 truncate">{{ admin.email }}{% if admin.id == current_user.id %} <span class="text-[10px] text-gray-400">(you)</span>{% endif %}</p>
                <p class="text-[11px] text-gray-400">Joined {{ admin.created_at.strftime('%d %b %Y') if admin.created_at else '—' }} &middot; Last sign-in {{ admin.last_login_at.strftime('%d %b %Y') if admin.last_login_at else 'never' }}</p>
            </div>
            {% if admin.id != current_user.id and admins|length > 1 %}
            <form method="POST" action="{{ url_port('team.remove_admin', id=admin.id) }}" onsubmit="return confirm({{ ('Remove ' ~ admin.email ~ ' as an admin?')|tojson|forceescape }})">
                {{ csrf_field() }}
                <button type="submit" class="px-3 py-1.5 rounded-lg text-xs font-semibold text-red-500 hover:bg-red-50">Remove</button>
            </form>
            {% endif %}
        </div>
        {% endfor %}
    </div>

    {% if open_invites %}
    <p class="text-[11px] font-bold text-gray-500 uppercase tracking-wider mb-2">Open invitations</p>
    <div class="space-y-2 mb-5">
        {% for invite in open_invites %}
        <div class="flex items-center justify-between gap-3 p-3 rounded-xl bg-white/60 border border-white/70">
            <div class="min-w-0">
                <p class="text-sm font-semibold text-gray-800 truncate">{{ invite.email }}</p>
                <p class="text-[11px] text-gray-400">Expires {{ invite.expires_at.strftime('%d %b %Y') }}</p>
            </div>
            <form method="POST" action="{{ url_port('team.cancel_invite', id=invite.id) }}">
                {{ csrf_field() }}
                <button type="submit" class="px-3 py-1.5 rounded-lg text-xs font-semibold text-gray-500 hover:bg-white/70">Cancel</button>
            </form>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    <form method="POST" action="{{ url_port('team.invite') }}" class="flex items-center gap-2">
        {{ csrf_field() }}
        <input type="email" name="email" placeholder="assistant@example.com" required class="candy-input flex-1 px-4 py-2.5 text-gray-900 focus:outline-none">
        <button type="submit" class="px-4 py-2.5 rounded-xl text-sm font-bold text-white bg-brand-500 hover:bg-brand-600 shadow-lg shadow-brand-200 whitespace-nowrap">Invite an admin</button>
    </form>

    {% if invite_url %}
    <div class="mt-4 p-4 rounded-xl bg-amber-50/70 border border-amber-100">
        <p class="text-xs font-bold text-amber-800 mb-2">Copy this link now &mdash; it is shown once and cannot be recovered.</p>
        <input type="text" readonly value="{{ invite_url }}" onclick="this.select()" class="w-full px-3 py-2 rounded-lg bg-white border border-amber-200 text-xs font-mono text-gray-700">
        <p class="text-[11px] text-amber-700 mt-2">Single use, expires in 7 days.</p>
    </div>
    {% endif %}
</div>
```

In `settings/index.html`, replace the `{# Team panel: ... #}` marker from Task 2 with `{% include 'settings/_team.html' %}`.

- [ ] **Step 8: `templates/auth/join.html`**

Same wrapper as `auth/setup.html`. Title `Join - GigLedger`. Heading "You've been invited to join {{ business.name or 'GigLedger' }}"; sub-line "for **{{ invite.email }}**". Form `method="POST" action="{{ url_port('team.join', token=token) }}"` with `{{ csrf_field() }}`, `password` (required, `minlength="12"`), `confirm_password` (required); submit "Join →". Footer line: "This invitation can be used once, and expires seven days after it was created."

- [ ] **Step 9: Run**

Run: `venv/bin/python -m pytest tests -q`
Expected: all PASS, including `test_csrf` (the three new POST routes reject token-less posts with 400) and `test_no_handbuilt_html`.

- [ ] **Step 10: Commit**

```bash
git add gigledger/models.py gigledger/team.py gigledger/routes/team.py gigledger/routes/settings.py gigledger/templates/settings tests/test_team.py gigledger/templates/auth/join.html
git commit -m "Add admins by invitation from a Team panel in Settings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: ADR, glossary, README

**Files:**
- Create: `docs/adr/0014-one-business-per-install.md`
- Modify: `docs/adr/README.md` (index line), `docs/glossary.md` (Portal Account / Portal Session entries, ~lines 203–225; any "tenant" wording), `README.md` (Client Portal section, remove sign-up mentions, add Team)
- Modify: `docs/superpowers/specs/2026-09-15-one-business-per-install-design.md` (status line)

- [ ] **Step 1: Write the ADR**

```markdown
# 0014. One business per install

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

GigLedger was written for one freelancer: every row carried a `user_id`, and
every route filtered on `user_id = current_user.id`, so two logins were two
businesses that could not see each other. The install this was built for is
one interior-design practice — a designer and perhaps an assistant or two —
and every one of them needs the same view of the same books.

The alternatives were a `Business` with members (multi-tenant, one more level
of scoping on every table) or a shared owner pointer (every admin filters on
one canonical user's id). Both keep machinery for a capability nobody asked
for; the second also makes "user" mean two things.

## Decision

One install keeps books for one business.

- A single-row `Business` holds the settings that are business-wide: name,
  address, phone, tax rate, currency, invoice prefix and counter, invoice note,
  categories. `Business.get()` is the one seam that fetches it.
- `User` is a login plus personal preferences (theme, dark mode).
- `@login_required` is the whole access rule on the admin side. No query
  filters on `user_id` for visibility.
- `user_id` columns stay and are stamped on new rows, as a record of who
  created a row. Nothing reads them for access, no UI shows them, and nothing
  cascades from `User` into them: removing an admin leaves their rows.
- Admins are added by invitation from Settings (same token rules as portal
  invites: returned once, stored hashed, 7-day TTL). Public sign-up is gone.
  A fresh install creates its first admin at `/setup`, which 404s once any
  admin exists.
- The demo seed runs only with `SEED_DEMO=1`; it creates an admin with a
  public password, which on a one-business install would see everything.

## Consequences

- ADR-0008's premise that ~40 routes scope rows by `user_id` is superseded.
  Its conclusion stands for a different reason: a portal account must never
  be `current_user` because `current_user` is an admin, and an admin sees
  everything. A portal session and an admin session remain mutually exclusive.
- The Client Portal's explicit-grant rule (ADR-0009) is now the only place in
  the app where two principals may see different rows.
- The portal home is headed by the business name and grouped by project;
  there is no longer an owner to group by.
- The security story on the admin side is simpler and must be stated as
  such: anyone with an admin login sees the whole business. Invitations are
  therefore the access-control decision, and the Team panel is where it is
  made and undone.
- Old business columns on `users` remain in existing databases, unread.
```

Add `- [0014](0014-one-business-per-install.md) — One business per install` to `docs/adr/README.md` in the same format as the existing lines.

- [ ] **Step 2: Glossary**

In `docs/glossary.md`:
- Add an entry **Business** before **Portal Account**: "`Business` — the one row holding business-wide settings. `Business.get()` fetches it; templates receive it as `business`. See ADR-0014."
- Add an entry **Admin** (or update any existing "Freelancer"/"User" entry): "An admin login (`User`). Every admin sees every row; `current_user` always means an admin. Added by invitation from Settings → Team; see ADR-0014."
- **Portal Account**: replace "linked many-to-one from tenant-scoped `Client` rows" with "linked many-to-one from `Client` rows"; delete the paragraph "The schema's only deliberately cross-tenant object … tenant-scoped data" and replace with "It holds credentials and nothing else; `portal_auth.visible_clients()` is the single seam between a login and the Client rows it may act through."
- **Portal Session**: replace the paragraph beginning "The distinction is not stylistic" with: "The distinction is not stylistic. `current_user` is an admin everywhere in this app, and an admin sees every row (ADR-0014); if a client could become `current_user`, a portal account would be an admin. A **Portal Session** and an admin session are mutually exclusive, so no request has two principals. See ADR-0008 and ADR-0014."
- `grep -n "tenant" docs/glossary.md` must return nothing afterwards.

- [ ] **Step 3: README**

- Remove any mention of signing up / creating an account from the getting-started text; replace with: first run opens `/setup` to create the business and the first admin.
- Add a **👥 Team** feature section after Client Portal:
  - **Every admin sees the same books** — one install, one business
  - **Invite-based** — Settings → Team creates a single-use link (7 days); you send it
  - **Remove an admin** — their entries stay, attributed to nobody
  - **No public sign-up** — `/setup` exists only until the first admin is created
  - pointer to ADR-0014
- In the Client Portal section, change "Documents grouped by who shared them" to "Documents grouped by project under the business name".
- Keep the `SEED_DEMO=1` line added in Task 7.

- [ ] **Step 4: Spec status and one correction**

In the spec, the sentence "The route is throttled by the same `LoginAttempt` scope machinery the two login forms use." (under *Admins are added by invitation*) is wrong for the same reason the portal's `/portal/invite/<token>` has no throttle: the token is 256 bits of randomness, not a password, so there is no dictionary to attack and nothing to throttle. Replace it with: "The route is not throttled: like the portal's redeem route, the token is a 256-bit random value, not a guessable secret."

Change the spec's `- **Status:** Approved, not yet implemented` to `- **Status:** Implemented 2026-09-15` (use the actual date of the final commit).

- [ ] **Step 5: Run the full suite one last time**

Run: `venv/bin/python -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0014-one-business-per-install.md docs/adr/README.md docs/glossary.md README.md docs/superpowers/specs/2026-09-15-one-business-per-install-design.md
git commit -m "Record ADR-0014: one business per install

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## After the plan: rollout on ledger.danismithdesign.com

Not a task for the implementer — a checklist for the operator, from the spec:

1. `cp gigledger.db gigledger.db.bak-$(date +%Y%m%d-%H%M%S)-pre-0014`
2. Restart the `proj@GigLedger` service (the first request runs `_migrate_db`, which seeds `business` from `demo@gigledger.com`'s columns: name "Demo Freelance Studio", prefix `INV`, counter 15).
3. Sign in as `dani@danismithdesign.com` → Settings → Business: rename to "Dani Smith Design".
4. Settings → Team: remove `demo@gigledger.com`.
5. Update `TEST_CREDENTIALS.md` (drop the demo row).
6. Confirm a portal login (Okafor or Raman) shows "Dani Smith Design" as the heading.
