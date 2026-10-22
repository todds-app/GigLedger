"""
Design Portal - Invoices Blueprint
"""
import math
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, Response, abort
from flask_login import login_required, current_user
from ..models import (db, Business, Invoice, InvoiceLineItem, Client, Transaction, EXPENSE,
                      INCOME, INVENTORY, TAX_RESERVE_CATEGORY, INVENTORY_SALES_CATEGORY,
                      SALE_SOURCE)

invoices_bp = Blueprint('invoices', __name__)


@invoices_bp.route('/invoices')
@login_required
def list_invoices():
    status_filter = request.args.get('status', 'all')

    query = Invoice.query
    if status_filter and status_filter != 'all':
        query = query.filter_by(status=status_filter)
    invoices = query.order_by(Invoice.created_at.desc()).all()

    # Summary calculations
    now = datetime.now()
    total_outstanding = sum(inv.total for inv in Invoice.query.filter(
        Invoice.status.in_(('sent', 'overdue'))).all())
    paid_this_month = sum(inv.total for inv in Invoice.query.filter_by(
        status='paid').all()
        if inv.paid_date and inv.paid_date.year == now.year and inv.paid_date.month == now.month)
    total_overdue = sum(inv.total for inv in Invoice.query.filter_by(
        status='overdue').all())

    # Sales tax collected on sold inventory: the client has paid it and the
    # state is owed it. It is never in the ledger, so this is the one place
    # that says how much to remit (docs/adr/0017).
    sales_tax_collected = sum(inv.sales_tax or 0 for inv in Invoice.query.filter_by(
        status='paid').all())

    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()

    return render_template('invoices/index.html',
        invoices=invoices,
        status_filter=status_filter,
        total_outstanding=total_outstanding,
        paid_this_month=paid_this_month,
        total_overdue=total_overdue,
        sales_tax_collected=sales_tax_collected,
        clients=clients,
        currency=Business.get().currency)


def _form_context(invoice=None):
    """What invoices/create.html needs, for a blank form or a draft to edit."""
    business = Business.get()
    return dict(
        invoice=invoice,
        clients=Client.query.filter_by(is_active=True).order_by(Client.name).all(),
        today=datetime.now().strftime('%Y-%m-%d'),
        default_due=(datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d'),
        tax_rate=business.default_tax_rate,
        currency=business.currency)


def _parse_invoice_form(form):
    """The create and edit forms post the same fields; read them once.

    Returns (client, issue_date, due_date, notes, action, lines). Each line
    is the InvoiceLineItem kwargs plus 'line_id' - the existing line it
    updates, or None for a row typed fresh. Rows without a description are
    dropped, so `lines` empty means "nothing to bill".
    """
    try:
        issue_date = datetime.strptime(form.get('issue_date', ''), '%Y-%m-%d')
    except ValueError:
        issue_date = datetime.now()
    try:
        due_date = datetime.strptime(form.get('due_date', ''), '%Y-%m-%d')
    except ValueError:
        due_date = issue_date + timedelta(days=30)

    # Resolve the client, ensuring the id is a real one. Referencing an id that
    # is not a client at all would leak whatever is at that id onto the invoice/PDF (IDOR).
    client_id = form.get('client_id', '')
    client = db.session.get(Client, int(client_id)) if client_id.isdigit() else None

    descriptions = form.getlist('description[]')
    quantities = form.getlist('quantity[]')
    rates = form.getlist('rate[]')
    sales_taxes = form.getlist('sales_tax[]')
    line_ids = form.getlist('line_id[]')
    lines = []
    for i, desc in enumerate(descriptions):
        desc = desc.strip()
        if not desc:
            continue
        try:
            qty = float(quantities[i]) if i < len(quantities) else 1
        except ValueError:
            qty = 1
        try:
            rate = float(rates[i]) if i < len(rates) else 0
        except ValueError:
            rate = 0
        # Sales tax on a sold item. Anything that is not a finite amount of
        # money is zero; `edit_invoice` zeroes it again on a line that sells nothing.
        try:
            sales_tax = float(sales_taxes[i]) if i < len(sales_taxes) else 0
        except ValueError:
            sales_tax = 0
        if not math.isfinite(sales_tax) or sales_tax < 0:
            sales_tax = 0
        line_id = line_ids[i] if i < len(line_ids) else ''
        lines.append({'line_id': int(line_id) if line_id.isdigit() else None,
                      'description': desc, 'quantity': qty, 'rate': rate, 'amount': qty * rate,
                      'sales_tax': sales_tax})

    return client, issue_date, due_date, form.get('notes', ''), form.get('action', 'draft'), lines


@invoices_bp.route('/invoices/create', methods=['GET'])
@login_required
def create_form():
    return render_template('invoices/create.html', **_form_context())


@invoices_bp.route('/invoices/create', methods=['POST'])
@login_required
def create_invoice():
    business = Business.get()
    client, issue_date, due_date, notes, action, lines = _parse_invoice_form(request.form)
    if not lines:
        flash('Please add at least one line item with a description.', 'error')
        return redirect(url_for('invoices.create_form'))

    status = 'sent' if action == 'send' else 'draft'
    invoice = Invoice(
        user_id=current_user.id,
        client_id=client.id if client else None,
        invoice_number=business.get_next_invoice_number(),
        status=status,
        issue_date=issue_date,
        due_date=due_date,
        notes=notes)
    for line in lines:
        line.pop('line_id')
        line['sales_tax'] = 0   # a typed line sells nothing (docs/adr/0017)
        invoice.line_items.append(InvoiceLineItem(**line))
    invoice.recalculate(business.default_tax_rate)
    db.session.add(invoice)
    db.session.commit()

    if status == 'sent':
        flash(f'Invoice {invoice.invoice_number} created and marked as sent!', 'success')
    else:
        flash(f'Invoice {invoice.invoice_number} saved as draft.', 'success')

    return redirect(url_for('invoices.detail', id=invoice.id))


def _editable_draft(id):
    """The draft to edit, or the redirect that explains why not."""
    invoice = db.session.get(Invoice, id)
    if not invoice:
        flash('Invoice not found.', 'error')
        return None, redirect(url_for('invoices.list_invoices'))
    if invoice.status != 'draft':
        flash('Only a draft can be edited.', 'error')
        return None, redirect(url_for('invoices.detail', id=id))
    return invoice, None


@invoices_bp.route('/invoices/<int:id>/edit', methods=['GET'])
@login_required
def edit_form(id):
    invoice, refusal = _editable_draft(id)
    if refusal:
        return refusal
    return render_template('invoices/create.html', **_form_context(invoice))


@invoices_bp.route('/invoices/<int:id>/edit', methods=['POST'])
@login_required
def edit_invoice(id):
    """Rewrite a draft from the form. Lines posted with a line_id update that
    line in place - a line billing a transaction keeps its link - new rows
    become new lines, and lines left off the form are deleted, which unlocks
    whatever they billed (docs/adr/0016)."""
    invoice, refusal = _editable_draft(id)
    if refusal:
        return refusal
    business = Business.get()
    client, issue_date, due_date, notes, action, lines = _parse_invoice_form(request.form)
    if not lines:
        flash('Please add at least one line item with a description.', 'error')
        return redirect(url_for('invoices.edit_form', id=id))

    invoice.client_id = client.id if client else None
    invoice.issue_date, invoice.due_date, invoice.notes = issue_date, due_date, notes

    # A posted line_id must be one of this invoice's own lines; anything
    # else is treated as a new row rather than reaching into another invoice.
    existing = {li.id: li for li in invoice.line_items}
    kept = []
    for line in lines:
        current = existing.get(line.pop('line_id'))
        if current is None:
            current = InvoiceLineItem()
            invoice.line_items.append(current)
        if not (current.transaction and current.transaction.is_inventory):
            line['sales_tax'] = 0   # only a sold item carries sales tax
        for field, value in line.items():
            setattr(current, field, value)
        kept.append(current)
    for line in list(invoice.line_items):
        if line not in kept:
            invoice.line_items.remove(line)

    invoice.recalculate(business.default_tax_rate)
    if action == 'send':
        invoice.status = 'sent'
    db.session.commit()

    if invoice.status == 'sent':
        flash(f'Invoice {invoice.invoice_number} updated and marked as sent!', 'success')
    else:
        flash(f'Invoice {invoice.invoice_number} updated.', 'success')
    return redirect(url_for('invoices.detail', id=invoice.id))


@invoices_bp.route('/invoices/status/<int:id>', methods=['POST'])
@login_required
def update_status(id):
    business = Business.get()
    invoice = Invoice.query.filter_by(id=id).first()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices.list_invoices'))

    new_status = request.form.get('status', '')
    valid_statuses = ['draft', 'sent', 'paid', 'overdue', 'cancelled']
    if new_status not in valid_statuses:
        flash('Invalid status.', 'error')
        return redirect(url_for('invoices.detail', id=id))

    # Marking as paid sets paid_date and sets the tax aside. It posts no
    # income: the invoice bills lines that are already in the ledger, so a
    # Client Payment row would count the same money twice (docs/adr/0016).
    # The exception is a sold inventory item, whose money is not in the
    # ledger yet: its purchase was cash out for an asset, so paying posts the
    # cost basis back and the profit as income (docs/adr/0017).
    sold = []
    if new_status == 'paid' and invoice.status != 'paid':
        invoice.paid_date = datetime.now()

        client_name = invoice.client.name if invoice.client else 'Unknown Client'

        for line in invoice.line_items:
            if line.transaction and line.transaction.is_inventory:
                rows = _sale_rows(invoice, line)
                db.session.add_all(rows)
                sold.append(rows)

        # Auto-create a tax reserve expense transaction for the tax portion
        # This ensures the tax owed on this invoice is explicitly set aside
        if invoice.tax_amount and invoice.tax_amount > 0:
            tax_transaction = Transaction(
                user_id=current_user.id,
                amount=-invoice.tax_amount,
                date=datetime.now(),
                kind=EXPENSE,
                category=TAX_RESERVE_CATEGORY,
                description=f'Tax reserve for Invoice {invoice.invoice_number} - {client_name} ({business.default_tax_rate*100:.0f}%)',
                is_tax_deductible=False,
                source='invoice',
                invoice_id=invoice.id)
            db.session.add(tax_transaction)

    # If moving away from paid, remove ALL linked transactions
    if invoice.status == 'paid' and new_status != 'paid':
        invoice.paid_date = None
        linked_txs = Transaction.query.filter_by(invoice_id=invoice.id).all()
        for linked_tx in linked_txs:
            db.session.delete(linked_tx)

    invoice.status = new_status
    db.session.commit()

    status_labels = {
        'draft': 'Draft', 'sent': 'Sent', 'paid': 'Paid',
        'overdue': 'Overdue', 'cancelled': 'Cancelled'
    }
    sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(business.currency, '$')
    notes = []
    if new_status == 'paid' and invoice.tax_amount and invoice.tax_amount > 0:
        notes.append(f'{sym}{invoice.tax_amount:,.2f} tax reserve auto-set aside.')
    if sold:
        profit = sum(row.amount for rows in sold for row in rows if row.kind != INVENTORY)
        word = 'profit' if profit >= 0 else 'loss'
        notes.append(f'{len(sold)} inventory item{"s" if len(sold) != 1 else ""} sold, '
                     f'{sym}{abs(profit):,.2f} {word} booked.')
    if notes:
        flash(f'Invoice {invoice.invoice_number} marked as Paid! ' + ' '.join(notes), 'success')
    else:
        flash(f'Invoice {invoice.invoice_number} marked as {status_labels.get(new_status, new_status)}.', 'success')
    return redirect(url_for('invoices.detail', id=id))


def _sale_rows(invoice, line):
    """The ledger rows paying `invoice` posts for the item `line` sells.

    Always the cost basis back as a positive inventory row - the asset has
    left the pool - then the difference: profit as income, a shortfall as a
    deductible expense, nothing at cost. Sales tax is on the invoice, not
    here: it is the client's money passing through to the state. All carry
    `invoice_id`, so reverting or deleting the invoice removes them.
    """
    tx = line.transaction
    item = tx.inventory_item
    cost, price = item.total_cost, line.amount or 0
    what = f'{tx.description or tx.category or "Item"} (Invoice {invoice.invoice_number})'
    common = dict(user_id=invoice.user_id, date=datetime.now(),
                  source=SALE_SOURCE, invoice_id=invoice.id)
    rows = [Transaction(kind=INVENTORY, amount=cost, category=tx.category,
                        description=f'Sold: {what}', is_tax_deductible=False, **common)]
    if price > cost:
        rows.append(Transaction(kind=INCOME, amount=price - cost,
                                category=INVENTORY_SALES_CATEGORY,
                                description=f'Profit on {what}',
                                is_tax_deductible=False, **common))
    elif price < cost:
        rows.append(Transaction(kind=EXPENSE, amount=-(cost - price),
                                category=INVENTORY_SALES_CATEGORY,
                                description=f'Loss on {what}',
                                is_tax_deductible=True, **common))
    return rows


@invoices_bp.route('/invoices/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    invoice = Invoice.query.filter_by(id=id).first()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices.list_invoices'))

    # Delete linked transactions
    linked_txs = Transaction.query.filter_by(invoice_id=invoice.id).all()
    for tx in linked_txs:
        db.session.delete(tx)

    inv_num = invoice.invoice_number
    db.session.delete(invoice)
    db.session.commit()
    flash(f'Invoice {inv_num} deleted.', 'success')
    return redirect(url_for('invoices.list_invoices'))


@invoices_bp.route('/invoices/<int:id>/lines/<int:line_id>/remove', methods=['POST'])
@login_required
def remove_line(id, line_id):
    """Take a line off a draft. This is how a billed transaction is unlocked
    without deleting the whole invoice (docs/adr/0016)."""
    line = db.session.get(InvoiceLineItem, line_id)
    if not line or line.invoice_id != id:
        abort(404)
    invoice = line.invoice
    if invoice.status != 'draft':
        flash('Only a draft invoice can lose a line.', 'error')
        return redirect(url_for('invoices.detail', id=id))
    invoice.line_items.remove(line)
    invoice.recalculate(Business.get().default_tax_rate)
    db.session.commit()
    flash('Line removed.', 'success')
    return redirect(url_for('invoices.detail', id=id))


@invoices_bp.route('/invoices/<int:id>')
@login_required
def detail(id):
    invoice = Invoice.query.filter_by(id=id).first()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices.list_invoices'))

    # Eagerly load line items
    line_items = InvoiceLineItem.query.filter_by(invoice_id=invoice.id).all()
    business = Business.get()

    return render_template('invoices/detail.html',
        invoice=invoice,
        line_items=line_items,
        currency=business.currency,
        tax_rate=business.default_tax_rate)


@invoices_bp.route('/invoices/<int:id>/pdf')
@login_required
def generate_pdf(id):
    invoice = Invoice.query.filter_by(id=id).first()
    if not invoice:
        flash('Invoice not found.', 'error')
        return redirect(url_for('invoices.list_invoices'))

    line_items = InvoiceLineItem.query.filter_by(invoice_id=invoice.id).all()

    sym = {'USD': '$', 'EUR': '\u20ac', 'GBP': '\u00a3',
           'CAD': 'C$', 'AUD': 'A$', 'INR': '\u20b9', 'JPY': '\u00a5'
           }.get(Business.get().currency, '$')

    status_colors = {
        'draft': '#6b7280', 'sent': '#3b82f6', 'paid': '#16a34a',
        'overdue': '#dc2626', 'cancelled': '#9ca3af'
    }

    # Rendered from a template, not built as an f-string, so that autoescape
    # applies to the client and business fields by default. See docs/adr/0005.
    html = render_template('invoices/export.html',
        invoice=invoice,
        line_items=line_items,
        sym=sym,
        status_color=status_colors.get(invoice.status, '#6b7280'))

    return Response(html, mimetype='text/html',
        headers={'Content-Disposition':
                 f'attachment; filename=invoice_{invoice.invoice_number}.html'})
