from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from ..models import TaxEstimate, Business, db
from ..finance import calculate_quarterly_tax, calculate_safe_to_spend, get_quarter

taxes_bp = Blueprint('taxes', __name__)


@taxes_bp.route('/taxes')
@login_required
def index():
    business = Business.get()
    tax_rate = business.default_tax_rate
    current_year = datetime.now().year
    current_quarter = get_quarter(datetime.now().month)

    quarters = []
    for q in range(1, 5):
        income, deductions, net, estimated_tax = calculate_quarterly_tax(q, current_year, tax_rate)
        quarters.append({'quarter': q, 'year': current_year, 'income': income,
            'deductions': deductions, 'net_income': net, 'estimated_tax': estimated_tax,
            'is_current': q == current_quarter})

    balance, tax_obligation, safe_balance = calculate_safe_to_spend(tax_rate)

    return render_template('taxes/index.html',
        quarters=quarters, current_year=current_year, current_quarter=current_quarter,
        tax_obligation=tax_obligation, safe_balance=safe_balance, balance=balance,
        tax_rate=tax_rate, currency=business.currency)


@taxes_bp.route('/taxes/calculate', methods=['POST'])
@login_required
def calculate():
    tax_rate = Business.get().default_tax_rate
    current_year = datetime.now().year

    for q in range(1, 5):
        income, deductions, net, estimated_tax = calculate_quarterly_tax(q, current_year, tax_rate)
        estimate = TaxEstimate.query.filter_by(quarter=q, year=current_year).first()
        if estimate:
            estimate.total_income = income
            estimate.total_deductions = deductions
            estimate.estimated_tax_owed = estimated_tax
        else:
            db.session.add(TaxEstimate(user_id=current_user.id, quarter=q, year=current_year,
                total_income=income, total_deductions=deductions, estimated_tax_owed=estimated_tax))

    db.session.commit()
    flash('Tax estimates recalculated!', 'success')
    return redirect(url_for('taxes.index'))
