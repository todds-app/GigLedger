"""
GigLedger - Invoices Blueprint
"""
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, Response
from flask_login import login_required, current_user
from ..models import db, Business, Invoice, InvoiceLineItem, Client, Transaction, INCOME, EXPENSE

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

    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()

    return render_template('invoices/index.html',
        invoices=invoices,
        status_filter=status_filter,
        total_outstanding=total_outstanding,
        paid_this_month=paid_this_month,
        total_overdue=total_overdue,
        clients=clients,
        currency=Business.get().currency)


@invoices_bp.route('/invoices/create', methods=['GET'])
@login_required
def create_form():
    business = Business.get()
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    today = datetime.now().strftime('%Y-%m-%d')
    default_due = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

    return render_template('invoices/create.html',
        clients=clients,
        today=today,
        default_due=default_due,
        tax_rate=business.default_tax_rate,
        currency=business.currency)


@invoices_bp.route('/invoices/create', methods=['POST'])
@login_required
def create_invoice():
    business = Business.get()
    client_id = request.form.get('client_id', '')
    issue_date_str = request.form.get('issue_date', '')
    due_date_str = request.form.get('due_date', '')
    notes = request.form.get('notes', '')
    action = request.form.get('action', 'draft')

    # Parse dates
    try:
        issue_date = datetime.strptime(issue_date_str, '%Y-%m-%d') if issue_date_str else datetime.now()
    except ValueError:
        issue_date = datetime.now()

    try:
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d') if due_date_str else issue_date + timedelta(days=30)
    except ValueError:
        due_date = issue_date + timedelta(days=30)

    # Parse line items
    descriptions = request.form.getlist('description[]')
    quantities = request.form.getlist('quantity[]')
    rates = request.form.getlist('rate[]')

    if not descriptions or not descriptions[0]:
        flash('Please add at least one line item.', 'error')
        return redirect(url_for('invoices.create_form'))

    # Calculate totals
    line_items_data = []
    subtotal = 0
    for i in range(len(descriptions)):
        desc = descriptions[i].strip()
        if not desc:
            continue
        try:
            qty = float(quantities[i]) if i < len(quantities) else 1
        except (ValueError, IndexError):
            qty = 1
        try:
            rate = float(rates[i]) if i < len(rates) else 0
        except (ValueError, IndexError):
            rate = 0
        amount = qty * rate
        subtotal += amount
        line_items_data.append({'description': desc, 'quantity': qty, 'rate': rate, 'amount': amount})

    if not line_items_data:
        flash('Please add at least one line item with a description.', 'error')
        return redirect(url_for('invoices.create_form'))

    tax_amount = subtotal * business.default_tax_rate
    total = subtotal + tax_amount

    # Resolve the client, ensuring the id is a real one. Referencing an id that
    # is not a client at all would leak whatever is at that id onto the invoice/PDF (IDOR).
    client_obj = None
    if client_id and client_id.isdigit():
        client_obj = db.session.get(Client, int(client_id))

    # Generate invoice number
    invoice_number = business.get_next_invoice_number()

    # Set status
    status = 'sent' if action == 'send' else 'draft'

    # Create invoice
    invoice = Invoice(
        user_id=current_user.id,
        client_id=client_obj.id if client_obj else None,
        invoice_number=invoice_number,
        status=status,
        issue_date=issue_date,
        due_date=due_date,
        notes=notes,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total)
    db.session.add(invoice)
    db.session.flush()  # Get the invoice ID

    # Create line items
    for item in line_items_data:
        li = InvoiceLineItem(
            invoice_id=invoice.id,
            description=item['description'],
            quantity=item['quantity'],
            rate=item['rate'],
            amount=item['amount'])
        db.session.add(li)

    db.session.commit()

    if status == 'sent':
        flash(f'Invoice {invoice_number} created and marked as sent!', 'success')
    else:
        flash(f'Invoice {invoice_number} saved as draft.', 'success')

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

    # If marking as paid, create transactions and set paid_date
    if new_status == 'paid' and invoice.status != 'paid':
        invoice.paid_date = datetime.now()

        client_name = invoice.client.name if invoice.client else 'Unknown Client'

        # 1. Create an income transaction for the full invoice total
        income_transaction = Transaction(
            user_id=current_user.id,
            amount=invoice.total,
            date=datetime.now(),
            kind=INCOME,
            category='Client Payment',
            description=f'Payment for Invoice {invoice.invoice_number} - {client_name}',
            is_tax_deductible=False,
            source='invoice',
            invoice_id=invoice.id)
        db.session.add(income_transaction)

        # 2. Auto-create a tax reserve expense transaction for the tax portion
        # This ensures the tax owed on this invoice is explicitly set aside
        if invoice.tax_amount and invoice.tax_amount > 0:
            tax_transaction = Transaction(
                user_id=current_user.id,
                amount=-invoice.tax_amount,
                date=datetime.now(),
                kind=EXPENSE,
                category='Tax Reserve',
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
    if new_status == 'paid' and invoice.tax_amount and invoice.tax_amount > 0:
        sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(business.currency, '$')
        flash(f'Invoice {invoice.invoice_number} marked as Paid! Income of {sym}{invoice.total:,.2f} recorded and {sym}{invoice.tax_amount:,.2f} tax reserve auto-set aside.', 'success')
    else:
        flash(f'Invoice {invoice.invoice_number} marked as {status_labels.get(new_status, new_status)}.', 'success')
    return redirect(url_for('invoices.detail', id=id))


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
