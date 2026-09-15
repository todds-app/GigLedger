from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import (Transaction, InventoryItem, Project, db, clean_kind,
                      EXPENSE, INCOME, INVENTORY, KINDS)

transactions_bp = Blueprint('transactions', __name__)


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


@transactions_bp.route('/transactions')
@login_required
def list_transactions():
    uid = current_user.id
    category = request.args.get('category', '')
    tx_type = request.args.get('type', '')
    month = request.args.get('month', '')
    year = request.args.get('year', '')

    transactions = _filtered_transactions(uid, request.args)

    categories = current_user.get_all_categories()
    categories.sort()

    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, current_user.default_tax_rate)

    return render_template('transactions/index.html',
        transactions=transactions, categories=categories,
        selected_category=category, selected_type=tx_type,
        selected_month=month, selected_year=year,
        currency=current_user.currency,
        user_categories=current_user.get_all_categories(),
        total_income=total_income, total_expenses=total_expenses,
        total_deductible=total_deductible, net=net, tax_saving=tax_saving)


@transactions_bp.route('/transactions/add', methods=['POST'])
@login_required
def add():
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

    # Calculate the tax impact of this transaction and give feedback
    if tx.is_expense and tx.is_tax_deductible:
        deduction = abs(tx.amount)
        tax_saving = deduction * current_user.default_tax_rate
        sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(current_user.currency, '$')
        flash(f'Transaction added! Tax deductible saves you ~{sym}{tax_saving:,.2f} in taxes at {current_user.default_tax_rate*100:.0f}% rate.', 'success')
    else:
        flash('Transaction added!', 'success')

    return redirect(request.referrer or url_for('dashboard.index'))


@transactions_bp.route('/transactions/edit/<int:id>', methods=['POST'])
@login_required
def edit(id):
    tx = Transaction.query.filter_by(id=id, user_id=current_user.id).first()
    if not tx:
        flash('Transaction not found.', 'error')
        return redirect(url_for('transactions.list_transactions'))

    try: amount = float(request.form.get('amount', '0'))
    except ValueError:
        flash('Invalid amount.', 'error')
        return redirect(url_for('transactions.list_transactions'))

    kind = clean_kind(request.form.get('type', tx.kind), fallback=tx.kind)
    if kind == EXPENSE and amount > 0: amount = -amount
    elif kind == INCOME and amount < 0: amount = abs(amount)
    tx.kind = kind

    date_str = request.form.get('date', '')
    try: tx.date = datetime.strptime(date_str, '%Y-%m-%d')
    except: pass

    tx.amount = amount
    tx.category = request.form.get('category', 'Uncategorized')
    tx.description = request.form.get('description', '')
    tx.is_tax_deductible = request.form.get('is_tax_deductible') == 'on'

    db.session.commit()
    flash('Transaction updated!', 'success')
    return redirect(url_for('transactions.list_transactions'))


@transactions_bp.route('/transactions/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    tx = Transaction.query.filter_by(id=id, user_id=current_user.id).first()
    if tx:
        db.session.delete(tx)
        db.session.commit()
        flash('Transaction deleted. Tax estimates will update on next calculation.', 'success')
    else:
        flash('Transaction not found.', 'error')
    return redirect(url_for('transactions.list_transactions'))


@transactions_bp.route('/transactions/export/csv')
@login_required
def export_csv():
    import csv, io
    uid = current_user.id

    transactions = _filtered_transactions(uid, request.args)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount', 'Tax Deductible'])

    for tx in transactions:
        writer.writerow([
            tx.date.strftime('%Y-%m-%d'),
            tx.kind_label,
            tx.category or 'Other',
            tx.description or '',
            f"{abs(tx.amount):.2f}",
            'Yes' if tx.is_tax_deductible else 'No'
        ])

    # Add summary rows
    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, current_user.default_tax_rate)

    writer.writerow([])
    writer.writerow(['--- SUMMARY ---'])
    writer.writerow(['Total Income', '', '', '', f"{total_income:.2f}"])
    writer.writerow(['Total Expenses', '', '', '', f"{total_expenses:.2f}"])
    writer.writerow(['Deductible Expenses', '', '', '', f"{total_deductible:.2f}"])
    writer.writerow(['Net', '', '', '', f"{net:.2f}"])
    writer.writerow(['Tax Saving from Deductions', '', '', '', f"{tax_saving:.2f}"])
    writer.writerow(['Tax Rate Used', '', '', '', f"{current_user.default_tax_rate*100:.0f}%"])

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
    uid = current_user.id

    transactions = _filtered_transactions(uid, request.args)

    # Build HTML for PDF
    sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(current_user.currency, '$')

    total_income, total_expenses, total_deductible, net, tax_saving = _totals(
        transactions, current_user.default_tax_rate)

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
