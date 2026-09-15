"""
GigLedger - Flask Application Factory
"""
import os
import uuid
from flask import Flask, request as req, url_for as _url_for, flash, redirect
from flask_login import LoginManager, current_user
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from markupsafe import Markup
from . import documents
from .models import db, User

# The database lives in the repository root, one level above this package, so
# that user data is not stored inside the importable source tree. Derived once
# and shared: create_app() and _migrate_db() must never disagree about which
# file they are opening, or a rename silently turns the migration into a no-op.
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(os.path.dirname(__file__))),
    'gigledger.db',
)

login_manager = LoginManager()
bcrypt = Bcrypt()
csrf = CSRFProtect()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'

CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'GBP': '£',
    'CAD': 'C$', 'AUD': 'A$', 'INR': '₹', 'JPY': '¥'
}


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')

    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Rejected by Flask before the request body is read, so an oversized upload
    # never reaches disk - a check inside the route would run too late. There is
    # deliberately no per-user quota: this is self-hosted and the operator owns
    # the disk. See ADR-0006.
    app.config['MAX_CONTENT_LENGTH'] = documents.MAX_UPLOAD_BYTES

    # SECRET_KEY must come from the environment. Never ship a hardcoded fallback:
    # the signing key for session/login cookies would be public and forgeable.
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        import secrets
        secret_key = secrets.token_hex(32)
        print("WARNING: SECRET_KEY is not set. Generated an ephemeral key for this "
              "process; sessions will not survive a restart. Set SECRET_KEY in production.")
    app.config['SECRET_KEY'] = secret_key

    # Cookie hardening. SameSite=Lax stops the session/remember cookies from being
    # sent on cross-site POSTs, which mitigates CSRF on the state-changing routes.
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    # Opt in to Secure cookies when served over HTTPS (kept off by default so local
    # HTTP deployments keep working). Set SESSION_COOKIE_SECURE=1 behind TLS.
    if os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['REMEMBER_COOKIE_SECURE'] = True

    db.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    # CSRF protection for every unsafe method, app-wide and on by default.
    # Exemptions must be explicit (@csrf.exempt) so they are visible in review;
    # tests/test_csrf.py walks the url_map and fails on any unguarded route.
    #
    # WTF_CSRF_SSL_STRICT is left at its default (on), which additionally checks
    # the Referer against the host on HTTPS requests. If this app is ever put
    # behind a TLS-terminating proxy that rewrites the host or port (see the
    # XTransformPort handling below), legitimate submissions can start failing
    # with no obvious cause. The fix is werkzeug's ProxyFix, not disabling this.
    csrf.init_app(app)

    @app.template_global()
    def csrf_field():
        """Hidden CSRF input. Defined once here so the markup has a single
        source of truth rather than being pasted into ~46 form bodies."""
        return Markup(
            f'<input type="hidden" name="csrf_token" value="{generate_csrf()}">'
        )

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        # Overwhelmingly this is a stale tab, not an attack: the token expired
        # or the session was reset. Redirect to a FIXED endpoint - never to
        # request.referrer, which would turn this handler into an open redirect.
        flash('That form had expired, so it was not submitted. Please try again.', 'error')
        if current_user.is_authenticated:
            return redirect(_url_for('dashboard.index')), 400
        return redirect(_url_for('auth.login')), 400

    # Custom Jinja2 filters
    @app.template_filter('money')
    def money_format(value):
        try:
            return f"{float(value):,.2f}"
        except Exception:
            return "0.00"

    @app.template_filter('decimal1')
    def decimal1_format(value):
        try:
            return f"{float(value):.1f}"
        except Exception:
            return "0.0"

    @app.template_filter('currency_symbol')
    def currency_symbol(code):
        return CURRENCY_SYMBOLS.get(code, '$')

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # After-request: inject XTransformPort into redirects
    @app.after_request
    def inject_transform_port(response):
        port = req.args.get('XTransformPort')
        if port and response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get('Location', '')
            if location and 'XTransformPort' not in location:
                sep = '&' if '?' in location else '?'
                response.headers['Location'] = f"{location}{sep}XTransformPort={port}"
        return response

    # Template context: url_port helper and xport variable
    @app.context_processor
    def inject_port():
        port = req.args.get('XTransformPort', '')

        def url_port(endpoint, **kwargs):
            url = _url_for(endpoint, **kwargs)
            if port:
                sep = '&' if '?' in url else '?'
                return f"{url}{sep}XTransformPort={port}"
            return url

        from .models import INVENTORY_CATEGORY_GUIDANCE, Business
        return {'xport': port, 'url_port': url_port, 'currency_symbols': CURRENCY_SYMBOLS,
                'inventory_guidance': INVENTORY_CATEGORY_GUIDANCE,
                'business': Business.get()}

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.dashboard import dashboard_bp
    from .routes.transactions import transactions_bp
    from .routes.taxes import taxes_bp
    from .routes.settings import settings_bp
    from .routes.clients import clients_bp
    from .routes.projects import projects_bp
    from .routes.reports import reports_bp
    from .routes.invoices import invoices_bp
    from .routes.goals import goals_bp
    from .routes.recurring import recurring_bp
    from .routes.inventory import inventory_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(taxes_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(clients_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(invoices_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(inventory_bp)

    # Registered conditionally, so switching the portal off removes the routes
    # rather than making them refuse. A route that exists and refuses is still a
    # surface, and still announces that the feature is there. See ADR-0008.
    from . import portal_auth
    if portal_auth.is_enabled():
        from .routes.portal import portal_bp
        app.register_blueprint(portal_bp)

    with app.app_context():
        db.create_all()
        _migrate_db(db)
        # Opt-in. The seed creates an admin with a public password, and on a
        # one-business install that admin sees everything (ADR-0014). A fresh
        # database without the flag starts unseeded.
        if os.environ.get('SEED_DEMO', '').lower() in ('1', 'true', 'yes'):
            _seed_demo_data()

    return app


def _migrate_db(db):
    """Add new columns to existing tables if they don't exist."""
    import sqlite3
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

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
    if 'custom_inventory_categories' not in user_columns and 'business_name' in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN custom_inventory_categories TEXT DEFAULT ''")

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

    # Migrate clients table - Client Portal access
    cursor.execute("PRAGMA table_info(clients)")
    client_columns = {row[1] for row in cursor.fetchall()}
    if 'portal_account_id' not in client_columns:
        cursor.execute("ALTER TABLE clients ADD COLUMN portal_account_id "
                       "INTEGER REFERENCES portal_accounts(id)")

    # Migrate portal_accounts table - "new since your last visit" on the portal
    # home. PRAGMA table_info on a table that does not exist yet returns no
    # rows, so a fresh install falls through to create_all() as usual.
    cursor.execute("PRAGMA table_info(portal_accounts)")
    pa_columns = {row[1] for row in cursor.fetchall()}
    if pa_columns and 'documents_seen_at' not in pa_columns:
        cursor.execute("ALTER TABLE portal_accounts ADD COLUMN documents_seen_at DATETIME")

    # Migrate project_documents table - who added it, when a portal client did
    cursor.execute("PRAGMA table_info(project_documents)")
    pd_columns = {row[1] for row in cursor.fetchall()}
    if pd_columns and 'added_by_client_id' not in pd_columns:
        cursor.execute("ALTER TABLE project_documents ADD COLUMN added_by_client_id "
                       "INTEGER REFERENCES clients(id)")

    # Migrate tax_estimates table - fix foreign key
    cursor.execute("PRAGMA table_info(tax_estimates)")
    te_columns = {row[1] for row in cursor.fetchall()}
    if 'user_id' not in te_columns:
        cursor.execute("ALTER TABLE tax_estimates ADD COLUMN user_id INTEGER REFERENCES users(id)")

    conn.commit()
    conn.close()


def _seed_demo_data():
    from .models import (User, Business, Transaction, Client, Invoice, InvoiceLineItem,
                          Project, Goal, RecurringTransaction)
    from flask_bcrypt import generate_password_hash

    if User.query.filter_by(email='demo@gigledger.com').first():
        return

    password_hash = generate_password_hash('demo1234').decode('utf-8')
    demo_user = User(email='demo@gigledger.com', password_hash=password_hash)
    db.session.add(demo_user)
    db.session.add(Business(
        name='Demo Freelance Studio',
        address='123 Creative Ave, San Francisco, CA 94102',
        phone='+1 (555) 123-4567',
        default_tax_rate=0.30, currency='USD',
        invoice_note='Payment due within 30 days. Thank you for your business!'))
    db.session.commit()

    from datetime import datetime, timedelta
    import random

    # ---- Create Clients ----
    clients_data = [
        {'name': 'Acme Corp', 'email': 'billing@acmecorp.com', 'phone': '+1 (555) 100-2000',
         'company': 'Acme Corporation', 'address': '456 Business Blvd, New York, NY 10001',
         'notes': 'Large enterprise client. Net-30 payment terms.'},
        {'name': 'StartupXYZ', 'email': 'finance@startupxyz.io', 'phone': '+1 (555) 200-3000',
         'company': 'StartupXYZ Inc', 'address': '789 Innovation Way, Austin, TX 73301',
         'notes': 'Monthly retainer. Fast-growing SaaS startup.'},
        {'name': 'Brew Co', 'email': 'hello@brewco.com', 'phone': '+1 (555) 300-4000',
         'company': 'Brew Co', 'address': '12 Hops Lane, Portland, OR 97201',
         'notes': 'Craft brewery. Logo and branding work.'},
        {'name': 'TechFlow', 'email': 'accounts@techflow.dev', 'phone': '+1 (555) 400-5000',
         'company': 'TechFlow Solutions', 'address': '321 Dev Street, Seattle, WA 98101',
         'notes': 'Backend API development project.'},
        {'name': 'HealthApp', 'email': 'design@healthapp.co', 'phone': '+1 (555) 500-6000',
         'company': 'HealthApp Inc', 'address': '55 Wellness Dr, Denver, CO 80201',
         'notes': 'UI/UX audit and redesign project.'},
        {'name': 'GreenCo', 'email': 'media@greenco.org', 'phone': '+1 (555) 600-7000',
         'company': 'GreenCo Sustainability', 'address': '88 Eco Lane, Boulder, CO 80301',
         'notes': 'Brand guidelines and sustainability report design.'},
        {'name': 'FinGroup', 'email': 'ops@fingroup.com', 'phone': '+1 (555) 700-8000',
         'company': 'FinGroup Advisory', 'address': '200 Finance St, Chicago, IL 60601',
         'notes': 'Weekly consulting calls.'},
        {'name': 'EduTech', 'email': 'dev@edutech.io', 'phone': '+1 (555) 800-9000',
         'company': 'EduTech Learning', 'address': '77 Campus Way, Boston, MA 02101',
         'notes': 'App prototype development.'},
    ]
    client_objects = []
    for cd in clients_data:
        c = Client(user_id=demo_user.id, **cd)
        db.session.add(c)
        client_objects.append(c)
    db.session.commit()

    # ---- Create Projects ----
    project_colors = ['#34d399', '#60a5fa', '#fbbf24', '#fb7185', '#a78bfa', '#f472b6', '#2dd4bf', '#fb923c']
    projects_data = [
        {'name': 'Website Redesign', 'client_id': client_objects[0].id, 'description': 'Complete redesign of corporate website with modern UI/UX',
         'status': 'completed', 'rate_type': 'fixed', 'rate': 12000, 'hours_logged': 100, 'color': project_colors[0],
         'start_date': datetime.now() - timedelta(days=120), 'end_date': datetime.now() - timedelta(days=30), 'deadline': datetime.now() - timedelta(days=30)},
        {'name': 'Monthly Retainer', 'client_id': client_objects[1].id, 'description': 'Ongoing development and design support',
         'status': 'active', 'rate_type': 'hourly', 'rate': 125, 'hours_logged': 84, 'color': project_colors[1],
         'start_date': datetime.now() - timedelta(days=90), 'deadline': datetime.now() + timedelta(days=60)},
        {'name': 'Brand Identity', 'client_id': client_objects[2].id, 'description': 'Logo, brand guidelines, and packaging design',
         'status': 'active', 'rate_type': 'fixed', 'rate': 8000, 'hours_logged': 60, 'color': project_colors[2],
         'start_date': datetime.now() - timedelta(days=45), 'deadline': datetime.now() + timedelta(days=15)},
        {'name': 'API Development', 'client_id': client_objects[3].id, 'description': 'RESTful API for payment processing system',
         'status': 'active', 'rate_type': 'hourly', 'rate': 150, 'hours_logged': 42, 'color': project_colors[3],
         'start_date': datetime.now() - timedelta(days=60), 'deadline': datetime.now() + timedelta(days=30)},
        {'name': 'UX Audit & Redesign', 'client_id': client_objects[4].id, 'description': 'Full UX audit with redesign recommendations',
         'status': 'on_hold', 'rate_type': 'fixed', 'rate': 5500, 'hours_logged': 20, 'color': project_colors[4],
         'start_date': datetime.now() - timedelta(days=30), 'deadline': datetime.now() + timedelta(days=45)},
        {'name': 'Consulting', 'client_id': client_objects[6].id, 'description': 'Weekly strategy and tech consulting',
         'status': 'active', 'rate_type': 'hourly', 'rate': 200, 'hours_logged': 36, 'color': project_colors[5],
         'start_date': datetime.now() - timedelta(days=60), 'deadline': None},
        {'name': 'App Prototype', 'client_id': client_objects[7].id, 'description': 'Interactive prototype for educational app',
         'status': 'active', 'rate_type': 'fixed', 'rate': 6500, 'hours_logged': 35, 'color': project_colors[6],
         'start_date': datetime.now() - timedelta(days=20), 'deadline': datetime.now() + timedelta(days=40)},
    ]
    for pd in projects_data:
        p = Project(user_id=demo_user.id, **pd)
        db.session.add(p)
    db.session.commit()

    # ---- Create Invoices ----
    now = datetime.now()
    tax_rate = Business.get().default_tax_rate  # 30%
    invoice_data = [
        # Paid invoices (past) — with proper tax amounts
        {'client_id': client_objects[0].id, 'status': 'paid', 'invoice_number': 'INV-0001',
         'issue_date': now - timedelta(days=90), 'due_date': now - timedelta(days=60),
         'paid_date': now - timedelta(days=55), 'subtotal': 4000, 'tax_amount': round(4000 * tax_rate, 2), 'total': round(4000 + 4000 * tax_rate, 2),
         'line_items': [('Website redesign - Phase 1', 1, 4000, 4000)]},
        {'client_id': client_objects[1].id, 'status': 'paid', 'invoice_number': 'INV-0002',
         'issue_date': now - timedelta(days=60), 'due_date': now - timedelta(days=30),
         'paid_date': now - timedelta(days=25), 'subtotal': 5250, 'tax_amount': round(5250 * tax_rate, 2), 'total': round(5250 + 5250 * tax_rate, 2),
         'line_items': [('Development hours - 42hrs', 42, 125, 5250)]},
        {'client_id': client_objects[2].id, 'status': 'paid', 'invoice_number': 'INV-0003',
         'issue_date': now - timedelta(days=45), 'due_date': now - timedelta(days=15),
         'paid_date': now - timedelta(days=10), 'subtotal': 3000, 'tax_amount': round(3000 * tax_rate, 2), 'total': round(3000 + 3000 * tax_rate, 2),
         'line_items': [('Logo design & brand guide - Phase 1', 1, 3000, 3000)]},
        {'client_id': client_objects[0].id, 'status': 'paid', 'invoice_number': 'INV-0004',
         'issue_date': now - timedelta(days=30), 'due_date': now,
         'paid_date': now - timedelta(days=5), 'subtotal': 8000, 'tax_amount': round(8000 * tax_rate, 2), 'total': round(8000 + 8000 * tax_rate, 2),
         'line_items': [('Website redesign - Phase 2', 1, 8000, 8000)]},
        # Sent (outstanding) invoices — with tax amounts
        {'client_id': client_objects[3].id, 'status': 'sent', 'invoice_number': 'INV-0005',
         'issue_date': now - timedelta(days=15), 'due_date': now + timedelta(days=15),
         'subtotal': 6300, 'tax_amount': round(6300 * tax_rate, 2), 'total': round(6300 + 6300 * tax_rate, 2),
         'line_items': [('API development - 42hrs', 42, 150, 6300)]},
        {'client_id': client_objects[1].id, 'status': 'sent', 'invoice_number': 'INV-0006',
         'issue_date': now - timedelta(days=10), 'due_date': now + timedelta(days=20),
         'subtotal': 5250, 'tax_amount': round(5250 * tax_rate, 2), 'total': round(5250 + 5250 * tax_rate, 2),
         'line_items': [('Development hours - 42hrs (March)', 42, 125, 5250)]},
        # Draft invoice — with tax amounts
        {'client_id': client_objects[6].id, 'status': 'draft', 'invoice_number': 'INV-0007',
         'issue_date': now, 'due_date': now + timedelta(days=30),
         'subtotal': 7200, 'tax_amount': round(7200 * tax_rate, 2), 'total': round(7200 + 7200 * tax_rate, 2),
         'line_items': [('Consulting hours - 36hrs', 36, 200, 7200)]},
        # Overdue invoice — with tax amounts
        {'client_id': client_objects[4].id, 'status': 'overdue', 'invoice_number': 'INV-0008',
         'issue_date': now - timedelta(days=45), 'due_date': now - timedelta(days=15),
         'subtotal': 2750, 'tax_amount': round(2750 * tax_rate, 2), 'total': round(2750 + 2750 * tax_rate, 2),
         'line_items': [('UX Audit - Phase 1', 1, 2750, 2750)]},
    ]
    invoice_objects = []
    for inv_data in invoice_data:
        li_data = inv_data.pop('line_items')
        inv = Invoice(user_id=demo_user.id, **inv_data)
        db.session.add(inv)
        db.session.flush()
        for desc, qty, rate, amt in li_data:
            db.session.add(InvoiceLineItem(invoice_id=inv.id, description=desc, quantity=qty, rate=rate, amount=amt))
        invoice_objects.append(inv)

        # Create transactions for paid invoices: income + auto tax reserve
        if inv.status == 'paid':
            client_name = next((c.name for c in client_objects if c.id == inv.client_id), 'Unknown Client')
            # Income transaction
            tx = Transaction(user_id=demo_user.id, amount=inv.total, date=inv.paid_date or inv.issue_date,
                           kind='income',
                           category='Client Payment', description=f'Payment for Invoice {inv.invoice_number} - {client_name}',
                           is_tax_deductible=False, source='invoice', invoice_id=inv.id)
            db.session.add(tx)
            # Tax reserve expense transaction (auto-set-aside)
            if inv.tax_amount and inv.tax_amount > 0:
                tax_tx = Transaction(user_id=demo_user.id, amount=-inv.tax_amount, date=inv.paid_date or inv.issue_date,
                                   kind='expense',
                                   category='Tax Reserve', description=f'Tax reserve for Invoice {inv.invoice_number} - {client_name} ({tax_rate*100:.0f}%)',
                                   is_tax_deductible=False, source='invoice', invoice_id=inv.id)
                db.session.add(tax_tx)
    db.session.commit()

    # The seed writes INV-0001..0008 by name; the counter must agree or the
    # first invoice created in the UI collides with INV-0001.
    Business.get().next_invoice_number = len(invoice_data) + 1
    db.session.commit()

    # ---- Create Transactions (same as before but more) ----
    cat_income = ['Client Payment', 'Consulting', 'Freelance Project', 'Royalty']
    cat_expense_deductible = ['Software', 'Internet', 'Office Supplies', 'Marketing']
    cat_expense_nondeduct = ['Travel', 'Equipment', 'Meal', 'Entertainment']

    desc_income = [
        'Website redesign for Acme Corp', 'Monthly retainer - StartupXYZ',
        'Logo design - Brew Co', 'Backend API - TechFlow', 'UI/UX audit - HealthApp',
        'Consulting call - FinGroup', 'E-commerce setup - ShopLocal', 'App prototype - EduTech',
        'Brand guidelines - GreenCo', 'Data visualization - AnalyticsPro',
        'Mobile app - FitTracker', 'SEO audit - LawFirm Associates',
        'Content strategy - MediaBuzz', 'WordPress theme - BakerDelight',
        'API integration - PayGate Inc'
    ]
    desc_expense_ded = [
        'Figma subscription', 'AWS hosting', 'Adobe Creative Cloud', 'Domain renewal',
        'Co-working space', 'Internet bill', 'Slack subscription', 'Notion Pro',
        'Email hosting', 'GitHub Pro', 'VS Code extensions', 'Cloud storage'
    ]
    desc_expense_non = [
        'Uber to client meeting', 'LinkedIn Premium', 'Conference ticket',
        'New mechanical keyboard', 'Coffee meetings', 'Printing services',
        'Team lunch', 'Co-working day pass', 'Uber ride', 'Parking fee'
    ]

    transactions = []
    for month_offset in range(5, -1, -1):
        month_date = now - timedelta(days=month_offset * 30)
        year = month_date.year
        month = month_date.month

        # 2-4 income transactions per month
        num_income = random.randint(2, 4)
        for _ in range(num_income):
            day = random.randint(1, min(28, month_date.day))
            tx_date = datetime(year, month, day, random.randint(9, 17), random.randint(0, 59))
            transactions.append(Transaction(
                user_id=demo_user.id,
                amount=round(random.uniform(800, 4500), 2),
                date=tx_date,
                kind='income',
                category=random.choice(cat_income),
                description=random.choice(desc_income),
                is_tax_deductible=False,
                source='manual'
            ))

        # 2-3 deductible expenses per month
        for _ in range(random.randint(2, 3)):
            day = random.randint(1, min(28, month_date.day))
            tx_date = datetime(year, month, day, random.randint(9, 17), random.randint(0, 59))
            transactions.append(Transaction(
                user_id=demo_user.id,
                amount=-round(random.uniform(15, 250), 2),
                date=tx_date,
                kind='expense',
                category=random.choice(cat_expense_deductible),
                description=random.choice(desc_expense_ded),
                is_tax_deductible=True,
                source='manual'
            ))

        # 1-2 non-deductible expenses per month
        for _ in range(random.randint(1, 2)):
            day = random.randint(1, min(28, month_date.day))
            tx_date = datetime(year, month, day, random.randint(9, 17), random.randint(0, 59))
            transactions.append(Transaction(
                user_id=demo_user.id,
                amount=-round(random.uniform(10, 150), 2),
                date=tx_date,
                kind='expense',
                category=random.choice(cat_expense_nondeduct),
                description=random.choice(desc_expense_non),
                is_tax_deductible=False,
                source='manual'
            ))

    db.session.add_all(transactions)
    db.session.commit()

    # ---- Create Project Documents ----
    # Both kinds, so the difference between a stored file and a reference is
    # visible without anyone having to create one. Deliberately no portal
    # account: the demo password is public, and a seeded credential would be a
    # second known password on an externally-facing login. Redeeming a real
    # invite is also the part of the feature a new user most needs to see.
    # See ADR-0009.
    from . import documents as documents_module
    from .models import ProjectDocument

    seeded_projects = Project.query.filter_by(user_id=demo_user.id).all()
    if seeded_projects:
        brief = ('Statement of work\n\nPhase 1: discovery and wireframes.\n'
                 'Phase 2: visual design and build.\n\nRates and schedule as agreed.\n')
        stored_name = f"{uuid.uuid4().hex}.txt"
        os.makedirs(documents_module.UPLOAD_ROOT, exist_ok=True)
        with open(documents_module.path_for(stored_name), 'w') as handle:
            handle.write(brief)

        db.session.add(ProjectDocument(
            user_id=demo_user.id, project_id=seeded_projects[0].id,
            kind='upload', title='Statement of work',
            stored_name=stored_name, original_name='statement-of-work.txt',
            byte_size=len(brief)))
        db.session.add(ProjectDocument(
            user_id=demo_user.id, project_id=seeded_projects[0].id,
            kind='link', title='Design review notes',
            external_url='https://docs.google.com/document/d/EXAMPLE/edit',
            provider='google_drive'))
        if len(seeded_projects) > 1:
            db.session.add(ProjectDocument(
                user_id=demo_user.id, project_id=seeded_projects[1].id,
                kind='link', title='Budget tracker',
                external_url='https://docs.google.com/spreadsheets/d/EXAMPLE/edit',
                provider='google_drive'))
        db.session.commit()

    # ---- Create Goals ----
    goals_data = [
        {'name': 'Emergency Fund', 'target_amount': 10000, 'current_amount': 4200,
         'deadline': now + timedelta(days=180), 'icon': 'shield', 'color': '#34d399',
         'is_completed': False},
        {'name': 'New Laptop', 'target_amount': 2500, 'current_amount': 1800,
         'deadline': now + timedelta(days=60), 'icon': 'laptop', 'color': '#60a5fa',
         'is_completed': False},
        {'name': 'Vacation Fund', 'target_amount': 5000, 'current_amount': 5000,
         'deadline': now + timedelta(days=90), 'icon': 'palm_tree', 'color': '#fbbf24',
         'is_completed': True},
        {'name': 'Tax Reserve Q2', 'target_amount': 8000, 'current_amount': 5200,
         'deadline': now + timedelta(days=30), 'icon': 'government', 'color': '#fb7185',
         'is_completed': False},
        {'name': 'Conference Budget', 'target_amount': 3000, 'current_amount': 900,
         'deadline': now + timedelta(days=120), 'icon': 'rocket', 'color': '#a78bfa',
         'is_completed': False},
    ]
    for gd in goals_data:
        g = Goal(user_id=demo_user.id, **gd)
        db.session.add(g)
    db.session.commit()

    # ---- Create Recurring Transactions ----
    recurring_data = [
        {'description': 'Figma Subscription', 'amount': -15.00, 'kind': 'expense', 'category': 'Software',
         'is_tax_deductible': True, 'frequency': 'monthly', 'day_of_month': 1,
         'is_active': True, 'next_date': datetime(now.year, now.month, 1) if now.day < 1 else datetime(now.year, now.month + 1 if now.month < 12 else 1, 1)},
        {'description': 'AWS Hosting', 'amount': -52.00, 'kind': 'expense', 'category': 'Software',
         'is_tax_deductible': True, 'frequency': 'monthly', 'day_of_month': 5,
         'is_active': True, 'next_date': datetime(now.year, now.month, 5) if now.day < 5 else datetime(now.year, now.month + 1 if now.month < 12 else 1, 5)},
        {'description': 'Co-working Space', 'amount': -250.00, 'kind': 'expense', 'category': 'Office Supplies',
         'is_tax_deductible': True, 'frequency': 'monthly', 'day_of_month': 1,
         'is_active': True, 'next_date': datetime(now.year, now.month, 1) if now.day < 1 else datetime(now.year, now.month + 1 if now.month < 12 else 1, 1)},
        {'description': 'Internet Bill', 'amount': -79.99, 'kind': 'expense', 'category': 'Internet',
         'is_tax_deductible': True, 'frequency': 'monthly', 'day_of_month': 15,
         'is_active': True, 'next_date': datetime(now.year, now.month, 15) if now.day < 15 else datetime(now.year, now.month + 1 if now.month < 12 else 1, 15)},
        {'description': 'Adobe Creative Cloud', 'amount': -54.99, 'kind': 'expense', 'category': 'Software',
         'is_tax_deductible': True, 'frequency': 'monthly', 'day_of_month': 10,
         'is_active': True, 'next_date': datetime(now.year, now.month, 10) if now.day < 10 else datetime(now.year, now.month + 1 if now.month < 12 else 1, 10)},
        {'description': 'Domain Renewal', 'amount': -12.99, 'kind': 'expense', 'category': 'Software',
         'is_tax_deductible': True, 'frequency': 'yearly', 'day_of_month': 1,
         'is_active': True, 'next_date': datetime(now.year + 1, 1, 1)},
        {'description': 'LinkedIn Premium', 'amount': -29.99, 'kind': 'expense', 'category': 'Marketing',
         'is_tax_deductible': False, 'frequency': 'monthly', 'day_of_month': 20,
         'is_active': False, 'next_date': None},
    ]
    for rd in recurring_data:
        rt = RecurringTransaction(user_id=demo_user.id, **rd)
        db.session.add(rt)
    db.session.commit()
