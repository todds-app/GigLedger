import math
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user
from ..models import (Transaction, InventoryItem, Project, Business, Client, Invoice,
                      InvoiceLineItem, db, clean_kind, INCOME, INVENTORY, KINDS)

transactions_bp = Blueprint('transactions', __name__)


def _filtered_transactions(args):
    """The transaction list the index and both exports share.

    Extracted while converting to kind because the filter existed in three
    copies. A fourth kind should be a change in one place, not three.
    """
    transactions = Transaction.query\
        .order_by(Transaction.date.desc()).all()

    # Income posted by paying an invoice is the same money as the lines the
    # invoice billed; the ledger, not the invoice, is the income record
    # (docs/adr/0016). Its tax reserve row stays: that is a real set-aside.
    transactions = [t for t in transactions
                    if not (t.source == 'invoice' and t.kind == INCOME)]

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


def _inventory_fields(form):
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
    if not (math.isfinite(quantity) and math.isfinite(unit_cost)) \
            or quantity <= 0 or unit_cost <= 0:
        raise InvalidInventory('Quantity and unit cost must be greater than zero.')

    project_id = None
    raw = (form.get('project_id') or '').strip()
    if raw:
        try:
            candidate = int(raw)
        except ValueError:
            raise InvalidInventory('Choose a project or General Inventory.')
        if not Project.query.filter_by(id=candidate).first():
            raise InvalidInventory('Choose a project or General Inventory.')
        project_id = candidate

    return quantity, unit_cost, form.get('is_consumable') == 'on', project_id


@transactions_bp.route('/transactions')
@login_required
def list_transactions():
    business = Business.get()
    category = request.args.get('category', '')
    tx_type = request.args.get('type', '')
    month = request.args.get('month', '')
    year = request.args.get('year', '')

    transactions = _filtered_transactions(request.args)

    categories = business.get_all_categories()
    categories.sort()

    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, business.default_tax_rate)

    return render_template('transactions/index.html',
        transactions=transactions, categories=categories,
        selected_category=category, selected_type=tx_type,
        selected_month=month, selected_year=year,
        currency=business.currency,
        user_categories=business.get_all_categories(),
        total_income=total_income, total_expenses=total_expenses,
        total_deductible=total_deductible, net=net, tax_saving=tax_saving,
        projects=Project.query.order_by(Project.name).all(),
        draft_invoices=Invoice.query.filter_by(status='draft').order_by(Invoice.created_at.desc()).all(),
        clients=Client.query.filter_by(is_active=True).order_by(Client.name).all())


@transactions_bp.route('/transactions/add', methods=['POST'])
@login_required
def add():
    business = Business.get()
    back = request.referrer or url_for('transactions.list_transactions')
    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)

    item_fields = None
    if kind == INVENTORY:
        try:
            item_fields = _inventory_fields(request.form)
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

    # Calculate the tax impact of this transaction and give feedback
    if tx.is_expense and tx.is_tax_deductible:
        deduction = abs(tx.amount)
        tax_saving = deduction * business.default_tax_rate
        sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(business.currency, '$')
        flash(f'Transaction added! Tax deductible saves you ~{sym}{tax_saving:,.2f} in taxes at {business.default_tax_rate*100:.0f}% rate.', 'success')
    else:
        flash('Transaction added!', 'success')

    return redirect(request.referrer or url_for('dashboard.index'))


@transactions_bp.route('/transactions/edit/<int:id>', methods=['POST'])
@login_required
def edit(id):
    tx = Transaction.query.filter_by(id=id).first()
    if not tx:
        flash('Transaction not found.', 'error')
        return redirect(url_for('transactions.list_transactions'))

    back = request.referrer or url_for('transactions.list_transactions')
    if tx.is_invoiced:
        flash(_locked_message(tx), 'error')
        return redirect(back)
    kind = clean_kind(request.form.get('type', tx.kind), fallback=tx.kind)

    # A purchase cannot become an expense, or an expense a purchase, by
    # editing. The item would have to be created or orphaned mid-edit, and
    # piece 3 needs a placed item never to quietly become a cost. One wall.
    if (kind == INVENTORY) != tx.is_inventory:
        flash('Delete and re-add to change an inventory purchase into an '
              'expense, or an expense into an inventory purchase.', 'error')
        return redirect(back)

    if kind == INVENTORY and tx.inventory_item is None:
        # A kind='inventory' row with no item cannot come from this app's
        # routes any more, but a database may already hold one. Repair is
        # delete-and-re-add, the same wall as a kind change (ADR-0011).
        flash('This inventory row has no item behind it. Delete it and add '
              'the purchase again.', 'error')
        return redirect(back)

    # Validate everything before writing anything, so a refused edit is a
    # no-op rather than a half-applied one.
    item_fields = None
    if kind == INVENTORY:
        try:
            item_fields = _inventory_fields(request.form)
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


@transactions_bp.route('/transactions/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    tx = Transaction.query.filter_by(id=id).first()
    if not tx:
        flash('Transaction not found.', 'error')
    elif tx.is_invoiced:
        flash(_locked_message(tx), 'error')
    else:
        db.session.delete(tx)
        db.session.commit()
        flash('Transaction deleted. Tax estimates will update on next calculation.', 'success')
    return redirect(url_for('transactions.list_transactions'))


def _locked_message(tx):
    return (f'This transaction is on invoice {tx.invoice_line.invoice.invoice_number}. '
            f'Remove it from the invoice first.')


@transactions_bp.route('/transactions/<int:id>/invoice', methods=['POST'])
@login_required
def add_to_invoice(id):
    """Bill a ledger line: on a new draft, or appended to an existing one.

    The link lives on the line item (docs/adr/0016), so the transaction the
    user typed is never touched by paying, reverting or deleting the invoice.
    """
    tx = db.session.get(Transaction, id) or abort(404)
    business = Business.get()
    back = redirect(request.referrer or url_for('transactions.list_transactions'))

    if tx.is_invoiced:
        flash(f'This transaction is already on invoice '
              f'{tx.invoice_line.invoice.invoice_number}.', 'error')
        return back
    if not tx.can_be_invoiced:
        flash('Only income and inventory transactions can be invoiced.', 'error')
        return back

    if request.form.get('target') == 'existing':
        invoice_id = request.form.get('invoice_id', '')
        invoice = db.session.get(Invoice, int(invoice_id)) if invoice_id.isdigit() else None
        if not invoice or invoice.status != 'draft':
            flash('Pick a draft invoice; only drafts take new lines.', 'error')
            return back
    else:
        # Same id check as invoices.create_invoice: a stray id must not
        # attach whatever row sits there to the invoice (IDOR).
        client_id = request.form.get('client_id', '')
        client = db.session.get(Client, int(client_id)) if client_id.isdigit() else None
        today = datetime.now()
        invoice = Invoice(user_id=current_user.id,
                          client_id=client.id if client else None,
                          invoice_number=business.get_next_invoice_number(),
                          status='draft', issue_date=today,
                          due_date=today + timedelta(days=30))
        db.session.add(invoice)
        db.session.flush()

    invoice.line_items.append(InvoiceLineItem(transaction_id=tx.id, **tx.as_line_item()))
    invoice.recalculate(business.default_tax_rate)
    db.session.commit()
    flash(f'Added to {invoice.invoice_number}.', 'success')
    return redirect(url_for('invoices.detail', id=invoice.id))


@transactions_bp.route('/transactions/export/csv')
@login_required
def export_csv():
    import csv, io
    business = Business.get()

    transactions = _filtered_transactions(request.args)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount', 'Tax Deductible', 'Invoiced'])

    for tx in transactions:
        writer.writerow([
            tx.date.strftime('%Y-%m-%d'),
            tx.kind_label,
            tx.category or 'Other',
            tx.description or '',
            f"{abs(tx.amount):.2f}",
            'Yes' if tx.is_tax_deductible else 'No',
            tx.invoice_line.invoice.invoice_number if tx.is_invoiced else ''
        ])

    # Add summary rows
    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, business.default_tax_rate)

    writer.writerow([])
    writer.writerow(['--- SUMMARY ---'])
    writer.writerow(['Total Income', '', '', '', f"{total_income:.2f}"])
    writer.writerow(['Total Expenses', '', '', '', f"{total_expenses:.2f}"])
    writer.writerow(['Deductible Expenses', '', '', '', f"{total_deductible:.2f}"])
    writer.writerow(['Net', '', '', '', f"{net:.2f}"])
    writer.writerow(['Tax Saving from Deductions', '', '', '', f"{tax_saving:.2f}"])
    writer.writerow(['Tax Rate Used', '', '', '', f"{business.default_tax_rate*100:.0f}%"])

    output.seek(0)
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=transactions_{datetime.now().strftime("%Y%m%d")}.csv'})


@transactions_bp.route('/transactions/export/pdf')
@login_required
def export_pdf():
    from flask import Response
    business = Business.get()

    transactions = _filtered_transactions(request.args)

    # Build HTML for PDF
    sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(business.currency, '$')

    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, business.default_tax_rate)

    # Rendered from a template, not built as an f-string, so that autoescape
    # applies to the description and category fields by default. See docs/adr/0005.
    generated_at = datetime.now()
    html = render_template('transactions/export.html',
        transactions=transactions,
        sym=sym,
        total_income=total_income,
        total_expenses=total_expenses,
        total_deductible=total_deductible,
        net=net,
        tax_saving=tax_saving,
        generated_at=generated_at)

    return Response(html, mimetype='text/html',
        headers={'Content-Disposition':
                 f'attachment; filename=transactions_{generated_at.strftime("%Y%m%d")}.html'})
