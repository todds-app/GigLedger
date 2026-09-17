"""Bill a ledger line on an invoice: the Invoiced column and Add to Invoice.

Recorded in docs/adr/0016. The ledger is the income record; an invoice is the
bill for lines already in it. A line item may point at the transaction it
bills (1:1, optional), the transaction is locked while it does, and marking an
invoice Paid no longer posts a Client Payment income row - only the tax reserve.
"""
import sqlite3
from datetime import datetime

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import (EXPENSE, INCOME, INVENTORY, Invoice, InvoiceLineItem,
                              InventoryItem, Transaction, db)
from conftest import login_as


def a_tx(app, kind=INCOME, amount=400, **overrides):
    with app.app_context():
        fields = dict(user_id=1, amount=amount, date=datetime(2026, 9, 10),
                      kind=kind, category='Consulting', description='Brand refresh',
                      source='manual')
        fields.update(overrides)
        tx = Transaction(**fields)
        db.session.add(tx)
        db.session.commit()
        return tx.id


def a_purchase(app, quantity=4, unit_cost=25):
    with app.app_context():
        tx = Transaction(user_id=1, amount=-(quantity * unit_cost), date=datetime(2026, 9, 10),
                         kind=INVENTORY, category='Seating', description='Folding chairs',
                         source='manual')
        tx.inventory_item = InventoryItem(user_id=1, quantity=quantity, unit_cost=unit_cost)
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


def put_on(app, tx_id, invoice_id):
    """Link by hand; the route that does it is tested below."""
    with app.app_context():
        tx = db.session.get(Transaction, tx_id)
        db.session.add(InvoiceLineItem(invoice_id=invoice_id, transaction_id=tx_id,
                                       **tx.as_line_item()))
        db.session.commit()


# --- Model -----------------------------------------------------------------

def test_a_fresh_income_transaction_is_not_invoiced_but_can_be(app):
    tid = a_tx(app)
    with app.app_context():
        tx = db.session.get(Transaction, tid)
        assert tx.is_invoiced is False
        assert tx.can_be_invoiced is True


def test_an_expense_cannot_be_invoiced(app):
    tid = a_tx(app, kind=EXPENSE, amount=-50)
    with app.app_context():
        assert db.session.get(Transaction, tid).can_be_invoiced is False


def test_a_linked_line_marks_the_transaction_invoiced(app):
    tid = a_tx(app)
    iid = a_draft(app)
    put_on(app, tid, iid)
    with app.app_context():
        tx = db.session.get(Transaction, tid)
        assert tx.is_invoiced is True
        assert tx.can_be_invoiced is False
        assert tx.invoice_line.invoice.id == iid


def test_income_bills_as_one_unit_at_its_amount(app):
    tid = a_tx(app, amount=400, description='Brand refresh')
    with app.app_context():
        assert db.session.get(Transaction, tid).as_line_item() == {
            'description': 'Brand refresh', 'quantity': 1, 'rate': 400, 'amount': 400}


def test_income_without_a_description_bills_under_its_category(app):
    tid = a_tx(app, description='', category='Consulting')
    with app.app_context():
        assert db.session.get(Transaction, tid).as_line_item()['description'] == 'Consulting'


def test_an_inventory_purchase_bills_as_quantity_times_unit_cost(app):
    tid = a_purchase(app, quantity=4, unit_cost=25)
    with app.app_context():
        assert db.session.get(Transaction, tid).as_line_item() == {
            'description': 'Folding chairs', 'quantity': 4, 'rate': 25, 'amount': 100}


def test_recalculate_rewrites_the_stored_totals_from_the_lines(app):
    iid = a_draft(app, subtotal=1, tax_amount=1, total=1)
    put_on(app, a_tx(app, amount=400), iid)
    put_on(app, a_tx(app, amount=100), iid)
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        inv.recalculate(0.25)
        assert (inv.subtotal, inv.tax_amount, inv.total) == (500, 125, 625)


# --- Migration -------------------------------------------------------------

def test_an_existing_line_items_table_gains_transaction_id(tmp_path, monkeypatch):
    monkeypatch.delenv('SEED_DEMO', raising=False)
    path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120), "
                 "password_hash VARCHAR(200))")
    conn.execute("CREATE TABLE invoice_line_items (id INTEGER PRIMARY KEY, invoice_id INTEGER, "
                 "description VARCHAR(300), quantity FLOAT, rate FLOAT, amount FLOAT)")
    conn.execute("INSERT INTO invoice_line_items VALUES (1, 1, 'Old line', 1, 10, 10)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(gigledger.app, 'DB_PATH', path)

    create_app()
    create_app()  # idempotent

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(invoice_line_items)")}
    assert 'transaction_id' in columns
    assert conn.execute("SELECT transaction_id FROM invoice_line_items").fetchone() == (None,)
    conn.close()


# --- Add to Invoice route --------------------------------------------------

def a_line_for(app, tx_id):
    with app.app_context():
        return InvoiceLineItem.query.filter_by(transaction_id=tx_id).one_or_none()


def the_invoice_for(app, tx_id):
    with app.app_context():
        return InvoiceLineItem.query.filter_by(transaction_id=tx_id).one().invoice


def test_adding_to_a_new_invoice_creates_a_draft_with_the_line(app):
    tid = a_tx(app, amount=400)
    with app.app_context():
        before = Invoice.query.count()
    resp = login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new'})
    with app.app_context():
        assert Invoice.query.count() == before + 1
        line = InvoiceLineItem.query.filter_by(transaction_id=tid).one()
        inv = line.invoice
        assert inv.status == 'draft'
        assert inv.client_id is None
        assert (line.quantity, line.rate, line.amount) == (1, 400, 400)
        assert inv.subtotal == 400
        assert inv.total == 400 + inv.tax_amount
        assert resp.status_code == 302 and resp.location.endswith(f'/invoices/{inv.id}')


def test_a_new_invoice_takes_the_chosen_client(app):
    tid = a_tx(app)
    login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new', 'client_id': '1'})
    assert the_invoice_for(app, tid).client_id == 1


def test_a_new_invoice_ignores_a_client_id_that_is_not_a_client(app):
    tid = a_tx(app)
    login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new', 'client_id': '9999'})
    assert the_invoice_for(app, tid).client_id is None


def test_new_invoices_take_the_next_number(app):
    first = a_tx(app)
    second = a_tx(app)
    client = login_as(app)
    client.post(f'/transactions/{first}/invoice', data={'target': 'new'})
    client.post(f'/transactions/{second}/invoice', data={'target': 'new'})
    numbers = {the_invoice_for(app, first).invoice_number,
               the_invoice_for(app, second).invoice_number}
    assert len(numbers) == 2


def test_adding_to_an_existing_draft_appends_and_recomputes(app):
    iid = a_draft(app)
    put_on(app, a_tx(app, amount=100), iid)
    tid = a_tx(app, amount=400)
    resp = login_as(app).post(f'/transactions/{tid}/invoice',
                              data={'target': 'existing', 'invoice_id': str(iid)})
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert [li.transaction_id for li in inv.line_items].count(tid) == 1
        assert inv.subtotal == 500
        assert inv.total == 500 + inv.tax_amount
    assert resp.status_code == 302 and resp.location.endswith(f'/invoices/{iid}')


def test_an_inventory_purchase_lands_as_quantity_times_unit_cost(app):
    iid = a_draft(app)
    tid = a_purchase(app, quantity=4, unit_cost=25)
    login_as(app).post(f'/transactions/{tid}/invoice',
                       data={'target': 'existing', 'invoice_id': str(iid)})
    line = a_line_for(app, tid)
    assert (line.quantity, line.rate, line.amount) == (4, 25, 100)


@pytest.mark.parametrize('status', ['sent', 'paid', 'overdue', 'cancelled'])
def test_only_a_draft_accepts_lines(app, status):
    iid = a_draft(app, status=status)
    tid = a_tx(app)
    resp = login_as(app).post(f'/transactions/{tid}/invoice',
                              data={'target': 'existing', 'invoice_id': str(iid)},
                              follow_redirects=True)
    assert a_line_for(app, tid) is None
    assert 'draft' in resp.get_data(as_text=True).lower()


def test_a_missing_draft_is_refused(app):
    tid = a_tx(app)
    login_as(app).post(f'/transactions/{tid}/invoice',
                       data={'target': 'existing', 'invoice_id': '9999'})
    assert a_line_for(app, tid) is None


def test_an_invoiced_transaction_is_not_added_twice(app):
    iid = a_draft(app)
    tid = a_tx(app)
    put_on(app, tid, iid)
    with app.app_context():
        invoices_before = Invoice.query.count()
    resp = login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new'},
                              follow_redirects=True)
    with app.app_context():
        assert Invoice.query.count() == invoices_before
        assert InvoiceLineItem.query.filter_by(transaction_id=tid).count() == 1
    assert 'already' in resp.get_data(as_text=True)


def test_an_expense_is_refused(app):
    tid = a_tx(app, kind=EXPENSE, amount=-50)
    with app.app_context():
        invoices_before = Invoice.query.count()
    login_as(app).post(f'/transactions/{tid}/invoice', data={'target': 'new'})
    with app.app_context():
        assert Invoice.query.count() == invoices_before
    assert a_line_for(app, tid) is None


def test_the_missing_transaction_is_a_404(app):
    assert login_as(app).post('/transactions/9999/invoice', data={'target': 'new'}).status_code == 404


# --- Locked while invoiced -------------------------------------------------

def test_editing_an_invoiced_transaction_is_refused(app):
    tid = a_tx(app, amount=400)
    put_on(app, tid, a_draft(app))
    resp = login_as(app).post(f'/transactions/edit/{tid}', follow_redirects=True,
                              data={'type': 'income', 'amount': '999', 'date': '2026-09-10',
                                    'category': 'Consulting', 'description': 'Changed'})
    with app.app_context():
        assert db.session.get(Transaction, tid).amount == 400
    assert 'on invoice' in resp.get_data(as_text=True)


def test_deleting_an_invoiced_transaction_is_refused(app):
    tid = a_tx(app)
    put_on(app, tid, a_draft(app))
    resp = login_as(app).post(f'/transactions/delete/{tid}', follow_redirects=True)
    with app.app_context():
        assert db.session.get(Transaction, tid) is not None
    assert 'on invoice' in resp.get_data(as_text=True)


def test_deleting_the_invoice_unlocks_the_transaction(app):
    tid = a_tx(app)
    iid = a_draft(app)
    put_on(app, tid, iid)
    login_as(app).post(f'/invoices/delete/{iid}')
    with app.app_context():
        tx = db.session.get(Transaction, tid)
        assert tx is not None
        assert tx.is_invoiced is False


# --- Removing a line from a draft -----------------------------------------

def test_removing_a_line_from_a_draft_unlocks_and_recomputes(app):
    iid = a_draft(app)
    keep = a_tx(app, amount=100)
    tid = a_tx(app, amount=400)
    put_on(app, keep, iid)
    put_on(app, tid, iid)
    with app.app_context():
        line_id = InvoiceLineItem.query.filter_by(transaction_id=tid).one().id
        db.session.get(Invoice, iid).recalculate(0)
        db.session.commit()
    resp = login_as(app).post(f'/invoices/{iid}/lines/{line_id}/remove')
    with app.app_context():
        assert db.session.get(Transaction, tid).is_invoiced is False
        assert db.session.get(Transaction, tid) is not None
        assert db.session.get(Invoice, iid).subtotal == 100
    assert resp.status_code == 302 and resp.location.endswith(f'/invoices/{iid}')


def test_a_sent_invoice_keeps_its_lines(app):
    iid = a_draft(app, status='sent')
    tid = a_tx(app)
    put_on(app, tid, iid)
    with app.app_context():
        line_id = InvoiceLineItem.query.filter_by(transaction_id=tid).one().id
    login_as(app).post(f'/invoices/{iid}/lines/{line_id}/remove')
    assert a_line_for(app, tid) is not None


def test_a_line_of_another_invoice_is_a_404(app):
    iid = a_draft(app)
    other = a_draft(app, invoice_number='INV-T2')
    tid = a_tx(app)
    put_on(app, tid, other)
    with app.app_context():
        line_id = InvoiceLineItem.query.filter_by(transaction_id=tid).one().id
    assert login_as(app).post(f'/invoices/{iid}/lines/{line_id}/remove').status_code == 404
    assert a_line_for(app, tid) is not None


# --- Paid posts only the tax reserve ---------------------------------------

def test_marking_paid_posts_no_income_row(app):
    iid = a_draft(app, status='sent', subtotal=400, tax_amount=100, total=500)
    tid = a_tx(app, amount=400)
    put_on(app, tid, iid)
    with app.app_context():
        income_before = Transaction.query.filter_by(kind=INCOME).count()
    login_as(app).post(f'/invoices/status/{iid}', data={'status': 'paid'})
    with app.app_context():
        assert Transaction.query.filter_by(kind=INCOME).count() == income_before
        reserve = Transaction.query.filter_by(invoice_id=iid).all()
        assert [(t.kind, t.category, t.amount) for t in reserve] == [(EXPENSE, 'Tax Reserve', -100)]


def test_reverting_paid_removes_the_reserve_but_not_the_billed_transaction(app):
    iid = a_draft(app, status='sent', subtotal=400, tax_amount=100, total=500)
    tid = a_tx(app, amount=400)
    put_on(app, tid, iid)
    client = login_as(app)
    client.post(f'/invoices/status/{iid}', data={'status': 'paid'})
    client.post(f'/invoices/status/{iid}', data={'status': 'sent'})
    with app.app_context():
        assert Transaction.query.filter_by(invoice_id=iid).count() == 0
        assert db.session.get(Transaction, tid) is not None


def test_the_demo_seed_posts_no_client_payment_rows(app):
    with app.app_context():
        assert Transaction.query.filter_by(source='invoice', kind=INCOME).count() == 0
        assert Transaction.query.filter_by(source='invoice', kind=EXPENSE).count() > 0


# --- Legacy invoice-posted payments are not listed -------------------------

def test_an_invoice_posted_payment_is_hidden_from_the_page_and_the_csv(app):
    a_tx(app, amount=777, source='invoice', category='Client Payment',
         description='Payment for Invoice INV-LEGACY')
    a_tx(app, kind=EXPENSE, amount=-70, source='invoice', category='Tax Reserve',
         description='Tax reserve for Invoice INV-LEGACY')
    client = login_as(app)
    page = client.get('/transactions').get_data(as_text=True)
    csv = client.get('/transactions/export/csv').get_data(as_text=True)
    assert 'Payment for Invoice INV-LEGACY' not in page
    assert 'Payment for Invoice INV-LEGACY' not in csv
    assert 'Tax reserve for Invoice INV-LEGACY' in page
    assert 'Tax reserve for Invoice INV-LEGACY' in csv


# --- The Invoiced column ---------------------------------------------------

def test_the_page_has_an_invoiced_column_and_a_button_for_unbilled_income(app):
    tid = a_tx(app, description='Unbilled work')
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert '>Invoiced<' in body
    assert f'openInvoiceModal({tid},' in body
    assert 'Add to Invoice' in body


def test_an_invoiced_row_shows_its_invoice_and_hides_edit_and_delete(app):
    tid = a_tx(app, description='Billed work')
    iid = a_draft(app, invoice_number='INV-PILL')
    put_on(app, tid, iid)
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert 'INV-PILL' in body
    assert f'/invoices/{iid}' in body
    assert f'openInvoiceModal({tid},' not in body
    assert f'/transactions/delete/{tid}' not in body
    assert f'openEditModal({tid},' not in body


def test_an_expense_row_has_no_invoice_button(app):
    tid = a_tx(app, kind=EXPENSE, amount=-50, description='Ink')
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert 'Ink' in body
    assert f'openInvoiceModal({tid},' not in body


def test_the_modal_lists_the_drafts_and_the_clients(app):
    a_draft(app, invoice_number='INV-DRAFT-X')
    a_draft(app, invoice_number='INV-SENT-X', status='sent')
    a_tx(app)
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert 'id="invoiceModal"' in body
    assert 'INV-DRAFT-X' in body
    assert 'INV-SENT-X' not in body
    assert 'name="client_id"' in body
    assert 'name="target"' in body


def test_the_inventory_page_hides_edit_for_an_invoiced_purchase(app):
    tid = a_purchase(app)
    client = login_as(app)
    assert f'openEditModal({tid},' in client.get('/inventory/').get_data(as_text=True)
    put_on(app, tid, a_draft(app, invoice_number='INV-STOCK'))
    body = client.get('/inventory/').get_data(as_text=True)
    assert f'openEditModal({tid},' not in body
    assert 'INV-STOCK' in body


def test_the_csv_has_an_invoiced_column(app):
    tid = a_tx(app, description='Billed work')
    put_on(app, tid, a_draft(app, invoice_number='INV-CSV'))
    csv = login_as(app).get('/transactions/export/csv').get_data(as_text=True)
    header, *rows = csv.strip().splitlines()
    assert 'Invoiced' in header
    assert any('Billed work' in row and 'INV-CSV' in row for row in rows)


def test_a_draft_offers_to_remove_a_line_and_a_sent_invoice_does_not(app):
    iid = a_draft(app)
    tid = a_tx(app)
    put_on(app, tid, iid)
    with app.app_context():
        line_id = InvoiceLineItem.query.filter_by(transaction_id=tid).one().id
    client = login_as(app)
    assert f'/invoices/{iid}/lines/{line_id}/remove' in client.get(f'/invoices/{iid}').get_data(as_text=True)
    with app.app_context():
        db.session.get(Invoice, iid).status = 'sent'
        db.session.commit()
    assert f'/lines/{line_id}/remove' not in client.get(f'/invoices/{iid}').get_data(as_text=True)


# --- The create form shares the totals arithmetic --------------------------

def test_the_create_form_stores_totals_from_its_lines(app):
    with app.app_context():
        from gigledger.models import Business
        Business.get().default_tax_rate = 0.25
        db.session.commit()
    resp = login_as(app).post('/invoices/create', data={
        'client_id': '1', 'issue_date': '2026-09-01', 'due_date': '2026-10-01',
        'action': 'draft', 'description[]': ['Design', 'Build', ''],
        'quantity[]': ['2', '1', ''], 'rate[]': ['100', '300', '']})
    assert resp.status_code == 302
    with app.app_context():
        inv = Invoice.query.order_by(Invoice.id.desc()).first()
        assert [(li.description, li.amount) for li in inv.line_items] == [('Design', 200), ('Build', 300)]
        assert (inv.subtotal, inv.tax_amount, inv.total) == (500, 125, 625)
        assert all(li.transaction_id is None for li in inv.line_items)


def test_the_modal_script_holds_the_route_as_plain_json(app):
    """Inside <script> HTML entities are not decoded, so the URL must be
    JSON, not HTML-escaped JSON (which is a JS syntax error)."""
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert 'var invoiceAction = "/transactions/0/invoice";' in body


# --- Editing a draft ------------------------------------------------------

def a_typed_line(app, invoice_id, description='Typed', quantity=1, rate=50):
    with app.app_context():
        line = InvoiceLineItem(invoice_id=invoice_id, description=description,
                               quantity=quantity, rate=rate, amount=quantity * rate)
        db.session.add(line)
        db.session.commit()
        return line.id


def line_id_for(app, tx_id):
    with app.app_context():
        return InvoiceLineItem.query.filter_by(transaction_id=tx_id).one().id


def edit_form(**lines):
    """Build the edit POST. `lines` is a list of (line_id, description, qty, rate)."""
    rows = lines.pop('rows', [])
    data = {'client_id': lines.pop('client_id', ''), 'issue_date': lines.pop('issue_date', '2026-09-01'),
            'due_date': lines.pop('due_date', '2026-10-01'), 'notes': lines.pop('notes', ''),
            'action': lines.pop('action', 'draft'),
            'line_id[]': [str(r[0]) if r[0] else '' for r in rows],
            'description[]': [r[1] for r in rows],
            'quantity[]': [str(r[2]) for r in rows],
            'rate[]': [str(r[3]) for r in rows]}
    return data


def test_the_edit_form_prefills_a_draft(app):
    iid = a_draft(app, client_id=1, notes='Net 30', invoice_number='INV-EDIT')
    tid = a_tx(app, amount=400, description='Billed work')
    put_on(app, tid, iid)
    lid = line_id_for(app, tid)
    body = login_as(app).get(f'/invoices/{iid}/edit').get_data(as_text=True)
    assert 'INV-EDIT' in body
    assert f'action="/invoices/{iid}/edit"' in body
    assert f'name="line_id[]" value="{lid}"' in body
    assert 'value="Billed work"' in body
    assert 'Net 30' in body
    assert '<option value="1" selected' in body
    assert 'from ledger' in body


def test_only_a_draft_has_an_edit_form(app):
    iid = a_draft(app, status='sent')
    resp = login_as(app).get(f'/invoices/{iid}/edit')
    assert resp.status_code == 302 and resp.location.endswith(f'/invoices/{iid}')


def test_editing_saves_client_dates_and_notes(app):
    iid = a_draft(app)
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(
        client_id='1', issue_date='2026-09-05', due_date='2026-09-20', notes='Thanks!',
        rows=[(None, 'Typed', 1, 50)]))
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert inv.client_id == 1
        assert inv.issue_date.date().isoformat() == '2026-09-05'
        assert inv.due_date.date().isoformat() == '2026-09-20'
        assert inv.notes == 'Thanks!'
        assert inv.status == 'draft'


def test_editing_a_linked_line_keeps_its_link_and_the_lock(app):
    iid = a_draft(app)
    tid = a_purchase(app, quantity=4, unit_cost=25)
    put_on(app, tid, iid)
    lid = line_id_for(app, tid)
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(
        rows=[(lid, 'Folding chairs (marked up)', 4, 40)]))
    with app.app_context():
        line = db.session.get(InvoiceLineItem, lid)
        assert (line.description, line.quantity, line.rate, line.amount) == ('Folding chairs (marked up)', 4, 40, 160)
        assert line.transaction_id == tid
        assert db.session.get(Transaction, tid).is_invoiced is True
        assert db.session.get(Invoice, iid).subtotal == 160


def test_dropping_a_linked_line_on_the_form_unlocks_the_transaction(app):
    iid = a_draft(app)
    tid = a_tx(app)
    put_on(app, tid, iid)
    keep = a_typed_line(app, iid)
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(rows=[(keep, 'Typed', 1, 50)]))
    with app.app_context():
        assert db.session.get(Transaction, tid).is_invoiced is False
        assert db.session.get(Transaction, tid) is not None
        assert [li.id for li in db.session.get(Invoice, iid).line_items] == [keep]


def test_editing_adds_new_lines_and_recomputes(app):
    iid = a_draft(app)
    keep = a_typed_line(app, iid, rate=100)
    with app.app_context():
        from gigledger.models import Business
        Business.get().default_tax_rate = 0.25
        db.session.commit()
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(
        rows=[(keep, 'Typed', 1, 100), (None, 'Extra', 2, 200)]))
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert sorted((li.description, li.amount) for li in inv.line_items) == [('Extra', 400), ('Typed', 100)]
        assert (inv.subtotal, inv.tax_amount, inv.total) == (500, 125, 625)


def test_a_line_id_from_another_invoice_is_ignored(app):
    iid = a_draft(app)
    other = a_draft(app, invoice_number='INV-T2')
    foreign = a_typed_line(app, other, description='Theirs')
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(rows=[(foreign, 'Hijacked', 1, 1)]))
    with app.app_context():
        assert db.session.get(InvoiceLineItem, foreign).description == 'Theirs'
        assert db.session.get(InvoiceLineItem, foreign).invoice_id == other
        assert [li.description for li in db.session.get(Invoice, iid).line_items] == ['Hijacked']


def test_save_and_send_from_the_edit_form_sends(app):
    iid = a_draft(app)
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(action='send', rows=[(None, 'Typed', 1, 50)]))
    with app.app_context():
        assert db.session.get(Invoice, iid).status == 'sent'


def test_an_edit_with_no_lines_is_refused(app):
    iid = a_draft(app)
    keep = a_typed_line(app, iid)
    resp = login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(rows=[]), follow_redirects=True)
    with app.app_context():
        assert [li.id for li in db.session.get(Invoice, iid).line_items] == [keep]
    assert 'line item' in resp.get_data(as_text=True)


def test_a_sent_invoice_refuses_the_edit_post(app):
    iid = a_draft(app, status='sent', notes='Original')
    login_as(app).post(f'/invoices/{iid}/edit', data=edit_form(notes='Changed', rows=[(None, 'X', 1, 1)]))
    with app.app_context():
        inv = db.session.get(Invoice, iid)
        assert inv.notes == 'Original'
        assert inv.line_items == []


def test_a_draft_offers_edit_and_a_sent_invoice_does_not(app):
    iid = a_draft(app)
    client = login_as(app)
    assert f'/invoices/{iid}/edit' in client.get(f'/invoices/{iid}').get_data(as_text=True)
    with app.app_context():
        db.session.get(Invoice, iid).status = 'sent'
        db.session.commit()
    assert f'/invoices/{iid}/edit' not in client.get(f'/invoices/{iid}').get_data(as_text=True)
