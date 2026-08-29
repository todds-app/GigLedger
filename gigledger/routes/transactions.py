from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from ..models import Transaction, db, clean_kind, EXPENSE, INCOME

transactions_bp = Blueprint('transactions', __name__)


@transactions_bp.route('/transactions')
@login_required
def list_transactions():
    uid = current_user.id
    category = request.args.get('category', '')
    tx_type = request.args.get('type', '')
    month = request.args.get('month', '')
    year = request.args.get('year', '')

    all_tx = Transaction.query.filter_by(user_id=uid).order_by(Transaction.date.desc()).all()

    transactions = all_tx
    if category:
        transactions = [t for t in transactions if t.category == category]
    if tx_type == 'income':
        transactions = [t for t in transactions if t.amount > 0]
    elif tx_type == 'expense':
        transactions = [t for t in transactions if t.amount < 0]
    if month:
        try:
            m = int(month)
            transactions = [t for t in transactions if t.date.month == m]
        except: pass
    if year:
        try:
            y = int(year)
            transactions = [t for t in transactions if t.date.year == y]
        except: pass

    categories = current_user.get_all_categories()
    categories.sort()

    return render_template('transactions/index.html',
        transactions=transactions, categories=categories,
        selected_category=category, selected_type=tx_type,
        selected_month=month, selected_year=year,
        currency=current_user.currency,
        user_categories=current_user.get_all_categories())


@transactions_bp.route('/transactions/add', methods=['POST'])
@login_required
def add():
    try: amount = float(request.form.get('amount', '0'))
    except ValueError:
        flash('Invalid amount.', 'error')
        return redirect(request.referrer or url_for('transactions.list_transactions'))

    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)
    if kind == EXPENSE and amount > 0: amount = -amount
    elif kind == INCOME and amount < 0: amount = abs(amount)

    date_str = request.form.get('date', '')
    try: date = datetime.strptime(date_str, '%Y-%m-%d')
    except: date = datetime.now()

    tx = Transaction(
        user_id=current_user.id, amount=amount, date=date, kind=kind,
        category=request.form.get('category', 'Uncategorized'),
        description=request.form.get('description', ''),
        is_tax_deductible=request.form.get('is_tax_deductible') == 'on',
        source='manual')
    db.session.add(tx)
    db.session.commit()

    # Calculate the tax impact of this transaction and give feedback
    if tx.is_tax_deductible and tx.amount < 0:
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

    kind = clean_kind(request.form.get('type', 'income'), fallback=INCOME)
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

    # Apply same filters as list view
    category = request.args.get('category', '')
    tx_type = request.args.get('type', '')
    month = request.args.get('month', '')
    year = request.args.get('year', '')

    all_tx = Transaction.query.filter_by(user_id=uid).order_by(Transaction.date.desc()).all()

    transactions = all_tx
    if category:
        transactions = [t for t in transactions if t.category == category]
    if tx_type == 'income':
        transactions = [t for t in transactions if t.amount > 0]
    elif tx_type == 'expense':
        transactions = [t for t in transactions if t.amount < 0]
    if month:
        try:
            m = int(month)
            transactions = [t for t in transactions if t.date.month == m]
        except: pass
    if year:
        try:
            y = int(year)
            transactions = [t for t in transactions if t.date.year == y]
        except: pass

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Type', 'Category', 'Description', 'Amount', 'Tax Deductible'])

    sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(current_user.currency, '$')

    for tx in transactions:
        writer.writerow([
            tx.date.strftime('%Y-%m-%d'),
            'Income' if tx.amount > 0 else 'Expense',
            tx.category or 'Other',
            tx.description or '',
            f"{abs(tx.amount):.2f}",
            'Yes' if tx.is_tax_deductible else 'No'
        ])

    # Add summary rows
    total_income = sum(t.amount for t in transactions if t.amount > 0)
    total_expenses = sum(abs(t.amount) for t in transactions if t.amount < 0)
    total_deductible = sum(abs(t.amount) for t in transactions if t.amount < 0 and t.is_tax_deductible)
    net = total_income - total_expenses
    tax_saving = total_deductible * current_user.default_tax_rate

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

    # Apply same filters
    category = request.args.get('category', '')
    tx_type = request.args.get('type', '')
    month = request.args.get('month', '')
    year = request.args.get('year', '')

    all_tx = Transaction.query.filter_by(user_id=uid).order_by(Transaction.date.desc()).all()
    transactions = all_tx
    if category:
        transactions = [t for t in transactions if t.category == category]
    if tx_type == 'income':
        transactions = [t for t in transactions if t.amount > 0]
    elif tx_type == 'expense':
        transactions = [t for t in transactions if t.amount < 0]
    if month:
        try:
            m = int(month)
            transactions = [t for t in transactions if t.date.month == m]
        except: pass
    if year:
        try:
            y = int(year)
            transactions = [t for t in transactions if t.date.year == y]
        except: pass

    # Build HTML for PDF
    sym = {'USD':'$','EUR':'€','GBP':'£','CAD':'C$','AUD':'A$','INR':'₹','JPY':'¥'}.get(current_user.currency, '$')

    total_income = sum(t.amount for t in transactions if t.amount > 0)
    total_expenses = sum(abs(t.amount) for t in transactions if t.amount < 0)
    total_deductible = sum(abs(t.amount) for t in transactions if t.amount < 0 and t.is_tax_deductible)
    net = total_income - total_expenses
    tax_saving = total_deductible * current_user.default_tax_rate

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
