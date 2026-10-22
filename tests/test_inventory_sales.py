"""Sell an inventory item to a client on an invoice.

Recorded in docs/adr/0017. The sale is the invoice line that bills the
purchase: sale price is the line amount, sales tax collected sits on the line
and in the invoice total (never in the ledger), and paying the invoice posts
the cost basis back as a positive inventory row plus the profit as income.
"""
import sqlite3
from datetime import datetime

import pytest

import design_portal.app
from design_portal.app import create_app
from design_portal.models import (EXPENSE, INCOME, INVENTORY, INVENTORY_SALES_CATEGORY,
                              SALE_SOURCE, Invoice, InvoiceLineItem, InventoryItem,
                              Project, Transaction, db)
from conftest import login_as


def a_purchase(app, quantity=4, unit_cost=25, project_id=None, **overrides):
    with app.app_context():
        fields = dict(user_id=1, amount=-(quantity * unit_cost), date=datetime(2026, 9, 10),
                      kind=INVENTORY, category='Seating', description='Folding chairs',
                      source='manual')
        fields.update(overrides)
        tx = Transaction(**fields)
        tx.inventory_item = InventoryItem(user_id=1, quantity=quantity, unit_cost=unit_cost,
                                          project_id=project_id)
        db.session.add(tx)
        db.session.commit()
        return tx.id


def a_draft(app, **overrides):
    with app.app_context():
        fields = dict(user_id=1, invoice_number='INV-T1', status='draft')
        fields.update(overrides)
        inv = Invoice(**fields)
        db.session.add(inv)
        db.session.commit()
        return inv.id


def sold_on(app, tx_id, invoice_id, price, sales_tax=0):
    """Link by hand; the route that does it is tested below."""
    with app.app_context():
        tx = db.session.get(Transaction, tx_id)
        db.session.add(InvoiceLineItem(invoice_id=invoice_id, transaction_id=tx_id,
                                       sales_tax=sales_tax, **tx.as_line_item(price)))
        db.session.get(Invoice, invoice_id).recalculate(0)
        db.session.commit()


def sell(client, tx_id, price, sales_tax=None, **extra):
    data = {'target': 'new', 'sale_price': str(price)}
    if sales_tax is not None:
        data.update(sales_tax_collected='on', sales_tax=str(sales_tax))
    data.update(extra)
    return client.post(f'/transactions/{tx_id}/invoice', data=data, follow_redirects=True)


def line_for(app, tx_id):
    with app.app_context():
        return InvoiceLineItem.query.filter_by(transaction_id=tx_id).one_or_none()


def posted_by(app, invoice_id):
    with app.app_context():
        rows = Transaction.query.filter_by(invoice_id=invoice_id).order_by(Transaction.id).all()
        return [(t.kind, t.category, t.amount, t.source, t.is_tax_deductible) for t in rows]


# --- Model -----------------------------------------------------------------

def test_a_purchase_bills_at_its_sale_price_spread_over_the_quantity(app):
    tid = a_purchase(app, quantity=4, unit_cost=25)
    with app.app_context():
        assert db.session.get(Transaction, tid).as_line_item(150) == {
            'description': 'Folding chairs', 'quantity': 4, 'rate': 37.5, 'amount': 150}


def test_recalculate_adds_the_lines_sales_tax_to_the_total(app):
    iid = a_draft(app)
    sold_on(app, a_purchase(app), iid, price=150, sales_tax=12)
    sold_on(app, a_purchase(app), iid, price=50, sales_tax=4)
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        inv.recalculate(0.25)
        assert (inv.subtotal, inv.tax_amount, inv.sales_tax, inv.total) == (200, 50, 16, 266)


def test_an_unsold_item_reports_no_sale(app):
    tid = a_purchase(app)
    with app.app_context():
        item = db.session.get(Transaction, tid).inventory_item
        assert item.is_sold is False
        assert item.sale_line is None
        assert item.sale_invoice is None
        assert (item.sale_price, item.sales_tax, item.profit) == (None, None, None)


def test_a_sold_item_reports_its_sale(app):
    tid = a_purchase(app, quantity=4, unit_cost=25)
    iid = a_draft(app)
    sold_on(app, tid, iid, price=150, sales_tax=12)
    with app.app_context():
        item = db.session.get(Transaction, tid).inventory_item
        assert item.is_sold is True
        assert item.sale_invoice.id == iid
        assert (item.sale_price, item.sales_tax, item.profit) == (150, 12, 50)


def test_a_row_posted_by_paying_an_invoice_knows_it(app):
    iid = a_draft(app)
    with app.app_context():
        posted = Transaction(user_id=1, amount=-10, date=datetime.now(), kind=EXPENSE,
                             category='Tax Reserve', source='invoice', invoice_id=iid)
        typed = Transaction(user_id=1, amount=-10, date=datetime.now(), kind=EXPENSE,
                            category='Software', source='manual')
        db.session.add_all([posted, typed])
        db.session.commit()
        assert posted.is_posted_by_invoice is True
        assert typed.is_posted_by_invoice is False


# --- Migration -------------------------------------------------------------

def test_existing_invoice_tables_gain_sales_tax(tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120), "
                 "password_hash VARCHAR(200))")
    conn.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, user_id INTEGER, "
                 "invoice_number VARCHAR(50), status VARCHAR(20), subtotal FLOAT, "
                 "tax_amount FLOAT, total FLOAT)")
    conn.execute("INSERT INTO invoices VALUES (1, 1, 'INV-1', 'paid', 100, 30, 130)")
    conn.execute("CREATE TABLE invoice_line_items (id INTEGER PRIMARY KEY, invoice_id INTEGER, "
                 "description VARCHAR(300), quantity FLOAT, rate FLOAT, amount FLOAT, "
                 "transaction_id INTEGER)")
    conn.execute("INSERT INTO invoice_line_items VALUES (1, 1, 'Old line', 1, 100, 100, NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(design_portal.app, 'DB_PATH', path)

    create_app()
    create_app()  # idempotent

    conn = sqlite3.connect(path)
    assert conn.execute("SELECT sales_tax FROM invoice_line_items").fetchone() == (0,)
    assert conn.execute("SELECT sales_tax FROM invoices").fetchone() == (0,)
    conn.close()


# --- Sell route ------------------------------------------------------------

def test_selling_puts_the_item_on_a_new_draft_at_the_sale_price(app):
    tid = a_purchase(app, quantity=4, unit_cost=25)
    with app.app_context():
        before = Invoice.query.count()
    resp = login_as(app).post(f'/transactions/{tid}/invoice',
                              data={'target': 'new', 'sale_price': '150',
                                    'sales_tax_collected': 'on', 'sales_tax': '12'})
    with app.app_context():
        assert Invoice.query.count() == before + 1
        line = InvoiceLineItem.query.filter_by(transaction_id=tid).one()
        assert (line.quantity, line.rate, line.amount, line.sales_tax) == (4, 37.5, 150, 12)
        inv = line.invoice
        assert inv.status == 'draft'
        assert (inv.subtotal, inv.sales_tax) == (150, 12)
        assert inv.total == 150 + inv.tax_amount + 12
        assert resp.status_code == 302 and resp.location.endswith(f'/invoices/{inv.id}')


def test_sales_tax_is_zero_unless_collected_is_ticked(app):
    tid = a_purchase(app)
    login_as(app).post(f'/transactions/{tid}/invoice',
                       data={'target': 'new', 'sale_price': '150', 'sales_tax': '12'})
    assert line_for(app, tid).sales_tax == 0


def test_a_new_draft_takes_the_posted_client(app):
    tid = a_purchase(app)
    sell(login_as(app), tid, 150, client_id='1')
    with app.app_context():
        assert InvoiceLineItem.query.filter_by(transaction_id=tid).one().invoice.client_id == 1


def test_selling_onto_an_existing_draft_appends(app):
    iid = a_draft(app)
    tid = a_purchase(app)
    login_as(app).post(f'/transactions/{tid}/invoice',
                       data={'target': 'existing', 'invoice_id': str(iid), 'sale_price': '150',
                             'sales_tax_collected': 'on', 'sales_tax': '12'})
    line = line_for(app, tid)
    assert (line.invoice_id, line.amount, line.sales_tax) == (iid, 150, 12)


@pytest.mark.parametrize('data', [
    {},                                                           # no price
    {'sale_price': '0'},
    {'sale_price': '-5'},
    {'sale_price': 'nan'},
    {'sale_price': 'abc'},
    {'sale_price': '150', 'sales_tax_collected': 'on'},           # ticked, no amount
    {'sale_price': '150', 'sales_tax_collected': 'on', 'sales_tax': '-1'},
    {'sale_price': '150', 'sales_tax_collected': 'on', 'sales_tax': 'inf'},
])
def test_a_sale_that_does_not_add_up_is_refused(app, data):
    tid = a_purchase(app)
    with app.app_context():
        before = Invoice.query.count()
    resp = login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new', **data},
                              follow_redirects=True)
    with app.app_context():
        assert Invoice.query.count() == before
    assert line_for(app, tid) is None
    assert 'sale' in resp.get_data(as_text=True).lower()


def test_income_ignores_the_sale_fields(app):
    with app.app_context():
        tx = Transaction(user_id=1, amount=400, date=datetime(2026, 9, 10), kind=INCOME,
                         category='Consulting', description='Brand refresh', source='manual')
        db.session.add(tx)
        db.session.commit()
        tid = tx.id
    sell(login_as(app), tid, 999, sales_tax=50)
    line = line_for(app, tid)
    assert (line.amount, line.sales_tax) == (400, 0)


def test_a_sold_item_is_not_sold_twice(app):
    tid = a_purchase(app)
    sold_on(app, tid, a_draft(app), price=150)
    resp = sell(login_as(app), tid, 200)
    with app.app_context():
        assert InvoiceLineItem.query.filter_by(transaction_id=tid).count() == 1
    assert 'already' in resp.get_data(as_text=True)


# --- Paid posts cost recovery and profit -----------------------------------

def a_sent_sale(app, price, sales_tax=0, quantity=4, unit_cost=25):
    """A sent invoice selling one purchase (cost 100 by default). Returns (invoice, tx)."""
    iid = a_draft(app, status='sent')
    tid = a_purchase(app, quantity=quantity, unit_cost=unit_cost)
    sold_on(app, tid, iid, price=price, sales_tax=sales_tax)
    return iid, tid


def test_paying_posts_the_cost_back_and_the_profit_as_income(app):
    iid, tid = a_sent_sale(app, price=150, sales_tax=12)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    assert posted_by(app, iid) == [
        (INVENTORY, 'Seating', 100, SALE_SOURCE, False),
        (INCOME, INVENTORY_SALES_CATEGORY, 50, SALE_SOURCE, False)]


def test_a_sale_below_cost_posts_the_loss_as_a_deductible_expense(app):
    iid, tid = a_sent_sale(app, price=80)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    assert posted_by(app, iid) == [
        (INVENTORY, 'Seating', 100, SALE_SOURCE, False),
        (EXPENSE, INVENTORY_SALES_CATEGORY, -20, SALE_SOURCE, True)]


def test_a_sale_at_cost_posts_only_the_cost_back(app):
    iid, tid = a_sent_sale(app, price=100)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    assert posted_by(app, iid) == [(INVENTORY, 'Seating', 100, SALE_SOURCE, False)]


def test_sales_tax_never_reaches_the_ledger(app):
    iid, tid = a_sent_sale(app, price=100, sales_tax=8)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    with app.app_context():
        assert not any(abs(t.amount) == 8 for t in Transaction.query.all())


def test_the_posted_rows_are_dated_today_and_name_the_sale(app):
    iid, tid = a_sent_sale(app, price=150)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    with app.app_context():
        rows = Transaction.query.filter_by(invoice_id=iid).all()
        assert all(t.date.date() == datetime.now().date() for t in rows)
        assert all('Folding chairs' in t.description and 'INV-T1' in t.description
                   for t in rows)


def test_the_tax_reserve_still_posts_alongside_the_sale(app):
    iid, tid = a_sent_sale(app, price=150)
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        inv.recalculate(0.30)
        db.session.commit()
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    kinds = [(k, c) for k, c, *_ in posted_by(app, iid)]
    assert (EXPENSE, 'Tax Reserve') in kinds
    assert (INCOME, INVENTORY_SALES_CATEGORY) in kinds


def test_reverting_paid_removes_the_sale_rows_but_keeps_the_purchase(app):
    iid, tid = a_sent_sale(app, price=150)
    client = login_as(app)
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    client.post(f'/invoices/status/{iid}', data={'status': 'sent'})
    with app.app_context():
        assert Transaction.query.filter_by(invoice_id=iid).count() == 0
        assert db.session.get(Transaction, tid).inventory_item.is_sold is True


def test_deleting_a_paid_invoice_removes_the_sale_rows_and_unsells(app):
    iid, tid = a_sent_sale(app, price=150)
    client = login_as(app)
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    client.post(f'/invoices/delete/{iid}')
    with app.app_context():
        assert Transaction.query.filter_by(invoice_id=iid).count() == 0
        item = db.session.get(Transaction, tid).inventory_item
        assert item is not None and item.is_sold is False


def test_paying_twice_does_not_post_twice(app):
    iid, tid = a_sent_sale(app, price=150)
    client = login_as(app)
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    assert len(posted_by(app, iid)) == 2


def test_the_profit_row_is_listed_on_the_transactions_page(app):
    iid, tid = a_sent_sale(app, price=150)
    client = login_as(app)
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    page = client.get('/transactions').get_data(as_text=True)
    assert INVENTORY_SALES_CATEGORY in page


# --- Rows posted by paying an invoice are locked ---------------------------

def a_posted_row(app):
    iid, tid = a_sent_sale(app, price=150)
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    with app.app_context():
        return Transaction.query.filter_by(invoice_id=iid, kind=INCOME).one().id


def test_editing_a_row_posted_by_an_invoice_is_refused(app):
    rid = a_posted_row(app)
    resp = login_as(app).post(f'/transactions/edit/{rid}', follow_redirects=True,
                              data={'type': 'income', 'amount': '999', 'date': '2026-09-10',
                                    'category': 'Consulting', 'description': 'Changed'})
    with app.app_context():
        assert db.session.get(Transaction, rid).amount == 50
    assert 'revert' in resp.get_data(as_text=True).lower()


def test_deleting_a_row_posted_by_an_invoice_is_refused(app):
    rid = a_posted_row(app)
    resp = login_as(app).post(f'/transactions/delete/{rid}', follow_redirects=True)
    with app.app_context():
        assert db.session.get(Transaction, rid) is not None
    assert 'revert' in resp.get_data(as_text=True).lower()


# --- Editing the draft -----------------------------------------------------

def draft_form(app, iid, **changes):
    """The draft's own lines, posted back as the edit form would."""
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        data = {'issue_date': '2026-09-10', 'due_date': '2026-10-10', 'action': 'draft',
                'line_id[]': [str(li.id) for li in inv.line_items],
                'description[]': [li.description for li in inv.line_items],
                'quantity[]': [str(li.quantity) for li in inv.line_items],
                'rate[]': [str(li.rate) for li in inv.line_items],
                'sales_tax[]': [str(li.sales_tax) for li in inv.line_items]}
    data.update(changes)
    return data


def test_editing_the_draft_changes_the_sales_tax_on_the_sale_line(app):
    iid = a_draft(app)
    tid = a_purchase(app)
    sold_on(app, tid, iid, price=150, sales_tax=12)
    login_as(app).post(f'/invoices/{iid}/edit', data=draft_form(app, iid, **{'sales_tax[]': ['9']}))
    assert line_for(app, tid).sales_tax == 9
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert inv.sales_tax == 9 and inv.total == 150 + inv.tax_amount + 9


def test_a_line_that_sells_nothing_cannot_carry_sales_tax(app):
    iid = a_draft(app)
    with app.app_context():
        db.session.add(InvoiceLineItem(invoice_id=iid, description='Design hours',
                                       quantity=2, rate=100, amount=200))
        db.session.commit()
    login_as(app).post(f'/invoices/{iid}/edit', data=draft_form(app, iid, **{'sales_tax[]': ['30']}))
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert [li.sales_tax for li in inv.line_items] == [0]
        assert inv.sales_tax == 0


@pytest.mark.parametrize('bad', ['-4', 'nan', 'abc', ''])
def test_a_sales_tax_that_does_not_add_up_becomes_zero(app, bad):
    iid = a_draft(app)
    tid = a_purchase(app)
    sold_on(app, tid, iid, price=150, sales_tax=12)
    login_as(app).post(f'/invoices/{iid}/edit', data=draft_form(app, iid, **{'sales_tax[]': [bad]}))
    assert line_for(app, tid).sales_tax == 0


def test_the_edit_form_offers_sales_tax_only_on_sale_lines(app):
    iid = a_draft(app)
    sold_on(app, a_purchase(app), iid, price=150, sales_tax=12)
    with app.app_context():
        db.session.add(InvoiceLineItem(invoice_id=iid, description='Design hours',
                                       quantity=2, rate=100, amount=200))
        db.session.commit()
    page = login_as(app).get(f'/invoices/{iid}/edit').get_data(as_text=True)
    # The sale line gets a visible input prefilled with its tax; the typed
    # line posts a hidden zero. (The add-row script carries a third, for new rows.)
    assert page.count('type="number" name="sales_tax[]" value="12.0"') == 1
    assert page.count('type="hidden" name="sales_tax[]" value="0"') == 2


# --- What the pages show ---------------------------------------------------

def test_a_sold_item_leaves_the_pool_totals(app):
    a_purchase(app, quantity=1, unit_cost=980, description='Sectional sofa')
    tid = a_purchase(app, quantity=1, unit_cost=220, description='Brass floor lamp')
    sold_on(app, tid, a_draft(app), price=300)
    page = login_as(app).get('/inventory/').get_data(as_text=True)
    assert '$980.00' in page          # asset value without the sold lamp
    assert '$1,200.00' not in page
    assert '$300.00' in page          # sold total
    assert 'Brass floor lamp' in page  # still listed, marked sold
    assert 'INV-T1' in page


def test_the_inventory_page_filters_sold_and_unsold(app):
    a_purchase(app, description='Sectional sofa')
    sold_on(app, a_purchase(app, description='Brass floor lamp'), a_draft(app), price=300)
    client = login_as(app)
    sold = client.get('/inventory/?status=sold').get_data(as_text=True)
    assert 'Brass floor lamp' in sold and 'Sectional sofa' not in sold
    unsold = client.get('/inventory/?status=unsold').get_data(as_text=True)
    assert 'Sectional sofa' in unsold and 'Brass floor lamp' not in unsold


def test_an_unsold_item_offers_sell_and_a_sold_one_shows_its_invoice(app):
    a_purchase(app, description='Sectional sofa')
    sold_on(app, a_purchase(app, description='Brass floor lamp'), a_draft(app), price=300)
    page = login_as(app).get('/inventory/').get_data(as_text=True)
    assert 'openInvoiceModal(' in page
    assert 'Sold' in page and 'INV-T1' in page


def test_project_detail_excludes_sold_items_from_its_total(app):
    with app.app_context():
        pid = Project.query.first().id
    a_purchase(app, quantity=1, unit_cost=150, project_id=pid, description='Throw pillows')
    sold_on(app, a_purchase(app, quantity=1, unit_cost=220, project_id=pid,
                            description='Brass floor lamp'), a_draft(app), price=300)
    page = login_as(app).get(f'/projects/{pid}').get_data(as_text=True)
    assert '$150.00' in page and '$370.00' not in page
    assert 'Brass floor lamp' in page and 'INV-T1' in page
    assert 'openInvoiceModal(' in page


def test_the_invoices_page_totals_sales_tax_collected_on_paid_invoices(app):
    paid, _ = a_sent_sale(app, price=150, sales_tax=12)
    login_as(app).post(f'/invoices/status/{paid}', data={'status': 'paid'})
    unpaid = a_draft(app, invoice_number='INV-T2', status='sent')
    sold_on(app, a_purchase(app), unpaid, price=150, sales_tax=99)
    page = login_as(app).get('/invoices').get_data(as_text=True)
    assert 'Sales tax collected' in page
    assert '$12.00' in page and '$99.00' not in page


def test_the_invoice_shows_the_sales_tax_row_only_when_there_is_some(app):
    iid = a_draft(app)
    tid = a_purchase(app)
    sold_on(app, tid, iid, price=150, sales_tax=12)
    client = login_as(app)
    assert 'Sales tax' in client.get(f'/invoices/{iid}').get_data(as_text=True)
    assert 'Sales tax' in client.get(f'/invoices/{iid}/pdf').get_data(as_text=True)
    plain = a_draft(app, invoice_number='INV-T2')
    sold_on(app, a_purchase(app), plain, price=150)
    assert 'Sales tax' not in client.get(f'/invoices/{plain}').get_data(as_text=True)


def test_reports_show_sales_tax_collected_for_the_year(app):
    paid, _ = a_sent_sale(app, price=150, sales_tax=12)
    login_as(app).post(f'/invoices/status/{paid}', data={'status': 'paid'})
    page = login_as(app).get(f'/reports?year={datetime.now().year}').get_data(as_text=True)
    assert 'Sales tax collected' in page and '$12.00' in page


# --- Settings --------------------------------------------------------------

@pytest.mark.parametrize('kind', ['income', 'expense'])
def test_the_inventory_sales_category_is_locked_because_the_app_writes_it(app, kind):
    client = login_as(app)
    client.post(f'/settings/categories/{kind}/rename',
                data={'category_name': INVENTORY_SALES_CATEGORY, 'new_name': 'Flips'})
    client.post(f'/settings/categories/{kind}/delete',
                data={'category_name': INVENTORY_SALES_CATEGORY})
    with app.app_context():
        from design_portal.models import Business
        categories = getattr(Business.get(), f'get_{kind}_categories')()
        assert INVENTORY_SALES_CATEGORY in categories
    body = client.get('/settings').get_data(as_text=True)
    assert f'name="category_name" value="{INVENTORY_SALES_CATEGORY}"' not in body


def test_a_row_posted_by_an_invoice_cannot_itself_be_invoiced(app):
    rid = a_posted_row(app)
    with app.app_context():
        assert db.session.get(Transaction, rid).can_be_invoiced is False
    with app.app_context():
        before = Invoice.query.count()
    login_as(app).post(f'/transactions/{rid}/invoice', data={'target': 'new'})
    with app.app_context():
        assert Invoice.query.count() == before
        assert InvoiceLineItem.query.filter_by(transaction_id=rid).count() == 0
