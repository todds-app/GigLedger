"""
GigLedger - SQLAlchemy Models
"""
from datetime import datetime
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
import json

db = SQLAlchemy()

# Default categories that ship with the app
DEFAULT_INCOME_CATEGORIES = ['Client Payment', 'Consulting', 'Freelance Project', 'Royalty']
DEFAULT_EXPENSE_CATEGORIES = ['Software', 'Internet', 'Office Supplies', 'Marketing',
                               'Travel', 'Equipment', 'Meal', 'Entertainment', 'Tax Reserve']

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
        # The `or ''` guard is load-bearing, not defensive dead code: SQLite
        # cannot add a NOT NULL constraint via ALTER TABLE, so `kind` is
        # nullable on any database migrated by _migrate_db regardless of what
        # the model declares. See ADR-0010's closing paragraph.
        return (self.kind or '').title()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    default_tax_rate = db.Column(db.Float, default=0.30)
    currency = db.Column(db.String(3), default='USD')
    custom_income_categories = db.Column(db.Text, default='')   # comma-separated
    custom_expense_categories = db.Column(db.Text, default='')  # comma-separated
    custom_inventory_categories = db.Column(db.Text, default='')  # comma-separated
    theme = db.Column(db.String(20), default='emerald')  # theme name
    dark_mode = db.Column(db.Boolean, default=False)      # dark mode toggle
    business_name = db.Column(db.String(200), default='')
    business_address = db.Column(db.Text, default='')
    business_phone = db.Column(db.String(50), default='')
    invoice_note = db.Column(db.Text, default='Thank you for your business!')
    invoice_prefix = db.Column(db.String(10), default='INV')
    next_invoice_number = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('Transaction', backref='user', lazy=True)
    tax_estimates = db.relationship('TaxEstimate', backref='user', lazy=True)
    clients = db.relationship('Client', backref='user', lazy=True)
    invoices = db.relationship('Invoice', backref='user', lazy=True)
    projects = db.relationship('Project', backref='user', lazy=True)
    goals = db.relationship('Goal', backref='user', lazy=True)
    recurring_transactions = db.relationship('RecurringTransaction', backref='user', lazy=True)

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


class Transaction(KindMixin, db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50))
    description = db.Column(db.String(200))
    is_tax_deductible = db.Column(db.Boolean, default=False)
    source = db.Column(db.String(20), default='manual')
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # The asset half of an inventory purchase; None for every other kind.
    # delete-orphan: the item has no meaning without its purchase.
    inventory_item = db.relationship('InventoryItem', back_populates='transaction',
                                     uselist=False, cascade='all, delete-orphan')


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


class TaxEstimate(db.Model):
    __tablename__ = 'tax_estimates'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    quarter = db.Column(db.Integer)
    year = db.Column(db.Integer)
    total_income = db.Column(db.Float)
    total_deductions = db.Column(db.Float)
    estimated_tax_owed = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Client(db.Model):
    __tablename__ = 'clients'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), default='')
    phone = db.Column(db.String(50), default='')
    company = db.Column(db.String(200), default='')
    address = db.Column(db.Text, default='')
    notes = db.Column(db.Text, default='')
    is_active = db.Column(db.Boolean, default=True)
    # Set when the client redeems a portal invite; cleared when access is
    # revoked. Null means "this client cannot log in", which is the default and
    # stays the default until the freelancer deliberately changes it.
    portal_account_id = db.Column(db.Integer, db.ForeignKey('portal_accounts.id'),
                                  nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    invoices = db.relationship('Invoice', backref='client', lazy=True)
    portal_invites = db.relationship('PortalInvite', backref='client', lazy=True,
                                     cascade='all, delete-orphan')
    projects = db.relationship('Project', backref='client', lazy=True)

    def total_invoiced(self):
        return sum(inv.total for inv in self.invoices if inv.status != 'draft')

    def total_paid(self):
        return sum(inv.total for inv in self.invoices if inv.status == 'paid')

    def total_outstanding(self):
        return sum(inv.total for inv in self.invoices if inv.status in ('sent', 'overdue'))


class PortalAccount(db.Model):
    """A client's login for the Client Portal.

    Deliberately **not** a User, and deliberately not loaded by Flask-Login. If
    a portal account could become `current_user`, every existing
    `filter_by(user_id=current_user.id)` in the app would match on its id and
    serve another tenant's rows. See docs/adr/0008.

    Also deliberately **global**, keyed by email rather than scoped to one
    freelancer: the same person is routinely a client of several freelancers,
    and one row per (freelancer, email) makes the login form ambiguous. This is
    the only cross-tenant object in the schema, and it holds credentials only -
    never documents, never project data. Everything a portal session can see is
    reached through the tenant-scoped Client rows linked to it.
    """
    __tablename__ = 'portal_accounts'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    # Bumped on revocation and on reissue. The session carries the value it saw
    # at login; a mismatch ends the session on the next request. Without this,
    # "revoke access" would mean "revoked whenever the cookie happens to lapse".
    session_epoch = db.Column(db.Integer, nullable=False, default=1)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    clients = db.relationship('Client', backref='portal_account', lazy=True)


class PortalInvite(db.Model):
    """A single-use, expiring grant that turns a Client into a login.

    The token is stored hashed. It is a bearer credential that will be pasted
    into email and chat, and a database read - a backup, a stray SELECT - must
    not hand over working invites.
    """
    __tablename__ = 'portal_invites'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    redeemed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def is_open(self):
        return self.redeemed_at is None and self.expires_at > datetime.utcnow()


class LoginAttempt(db.Model):
    """Failed logins, for throttling. Durable rather than in-memory so a restart
    is not a way to clear the counter, and so it works with more than one worker
    process."""
    __tablename__ = 'login_attempts'

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(20), nullable=False)  # portal, app
    identifier = db.Column(db.String(200), nullable=False, index=True)
    ip = db.Column(db.String(64), default='')
    at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Invoice(db.Model):
    __tablename__ = 'invoices'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True)
    invoice_number = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='draft')  # draft, sent, paid, overdue, cancelled
    issue_date = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, default='')
    subtotal = db.Column(db.Float, default=0)
    tax_amount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    paid_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    line_items = db.relationship('InvoiceLineItem', backref='invoice', lazy=True, cascade='all, delete-orphan')
    transactions = db.relationship('Transaction', backref='invoice_ref', lazy=True)


class InvoiceLineItem(db.Model):
    __tablename__ = 'invoice_line_items'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoices.id'), nullable=False)
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Float, default=1)
    rate = db.Column(db.Float, default=0)
    amount = db.Column(db.Float, default=0)


class Project(db.Model):
    __tablename__ = 'projects'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='active')  # active, completed, on_hold, cancelled
    rate_type = db.Column(db.String(20), default='hourly')  # hourly, fixed, daily
    rate = db.Column(db.Float, default=0)
    hours_logged = db.Column(db.Float, default=0)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    end_date = db.Column(db.DateTime, nullable=True)
    deadline = db.Column(db.DateTime, nullable=True)
    color = db.Column(db.String(20), default='#34d399')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    documents = db.relationship('ProjectDocument', backref='project', lazy=True,
                                cascade='all, delete-orphan')

    @property
    def earned(self):
        if self.rate_type == 'hourly':
            return self.hours_logged * self.rate
        elif self.rate_type == 'daily':
            return self.hours_logged * self.rate
        else:  # fixed
            if self.status == 'completed':
                return self.rate
            return self.hours_logged * (self.rate / 100) if self.rate > 0 else 0

    @property
    def progress(self):
        if self.rate_type == 'fixed' and self.rate > 0:
            return min(100, (self.hours_logged / 100) * 100)
        return 0


class ProjectDocument(db.Model):
    """A file or a link attached to a project.

    One table with a `kind` discriminator rather than two tables: an upload and
    a link differ only in how the content is fetched, and every other operation
    - listing, sharing, deleting, authorising - is identical. Two tables would
    mean writing the authorisation check twice, and the second copy is where it
    gets forgotten. See docs/adr/0006.

    The per-kind columns are nullable because they are per-kind; `kind` is the
    column that says which set is meaningful.
    """
    __tablename__ = 'project_documents'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id'), nullable=False)
    kind = db.Column(db.String(20), nullable=False)  # upload, link
    title = db.Column(db.String(200), nullable=False)

    # kind == 'upload'
    stored_name = db.Column(db.String(80), nullable=True)   # generated; the name on disk
    original_name = db.Column(db.String(255), nullable=True)  # user's name; display only
    byte_size = db.Column(db.Integer, nullable=True)

    # kind == 'link'
    external_url = db.Column(db.Text, nullable=True)
    provider = db.Column(db.String(20), nullable=True)  # google_drive, other

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    shares = db.relationship('DocumentShare', backref='document', lazy=True,
                             cascade='all, delete-orphan')
    accesses = db.relationship('DocumentAccess', backref='document', lazy=True,
                               cascade='all, delete-orphan')

    @property
    def shared_client_ids(self):
        return {s.client_id for s in self.shares}

    @property
    def is_link(self):
        return self.kind == 'link'

    @property
    def size_label(self):
        size = self.byte_size or 0
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"


class DocumentShare(db.Model):
    """A grant of one document to one client.

    Default-private: a document with no rows here is visible to its owner alone,
    so a mis-click leaks nothing because there is nothing to mis-click into.

    A share names a `Client`, not a `PortalAccount`. The grant is made by a
    freelancer to a client of theirs, and stays meaningful whether or not that
    client has ever signed in - revoking portal access unlinks the account and
    leaves the grants intact, ready if access is granted again.
    """
    __tablename__ = 'document_shares'
    __table_args__ = (db.UniqueConstraint('document_id', 'client_id',
                                          name='uq_document_share'),)

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('project_documents.id'),
                            nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DocumentAccess(db.Model):
    """One row per document actually fetched.

    Exactly one of `user_id` / `portal_account_id` is set, saying which kind of
    principal read it. Written only after authorisation succeeds, so the table
    is a record of access rather than of attempts - a refused request is not an
    access and must not read like one.
    """
    __tablename__ = 'document_accesses'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('project_documents.id'),
                            nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    portal_account_id = db.Column(db.Integer, db.ForeignKey('portal_accounts.id'),
                                  nullable=True)
    ip = db.Column(db.String(64), default='')
    at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Goal(db.Model):
    __tablename__ = 'goals'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    target_amount = db.Column(db.Float, nullable=False)
    current_amount = db.Column(db.Float, default=0)
    deadline = db.Column(db.DateTime, nullable=True)
    icon = db.Column(db.String(50), default='target')
    color = db.Column(db.String(20), default='#34d399')
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def progress_percent(self):
        if self.target_amount <= 0:
            return 0
        return min(100, (self.current_amount / self.target_amount) * 100)

    @property
    def remaining(self):
        return max(0, self.target_amount - self.current_amount)


class RecurringTransaction(KindMixin, db.Model):
    __tablename__ = 'recurring_transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    category = db.Column(db.String(50), default='')
    is_tax_deductible = db.Column(db.Boolean, default=False)
    frequency = db.Column(db.String(20), default='monthly')  # weekly, monthly, quarterly, yearly
    day_of_month = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    last_generated = db.Column(db.DateTime, nullable=True)
    next_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
