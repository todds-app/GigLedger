"""
FreelanceCash - Core Financial Logic

Implements:
  A. The "Safe to Spend" Formula
  B. Quarterly Tax Estimator
  C. Runway Calculator
"""
from datetime import datetime
from .models import Transaction, db


def get_quarter(month):
    """Return the fiscal quarter (1-4) for a given month."""
    return (month - 1) // 3 + 1


def get_quarter_date_range(quarter, year):
    """Return (start_date, end_date) for a given quarter/year.
    end_date is exclusive (first day of next quarter)."""
    quarters = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
    start_month, end_month = quarters[quarter]
    start_date = datetime(year, start_month, 1)
    if end_month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, end_month + 1, 1)
    return start_date, end_date


def _get_tx_range(user_id, start_date, end_date):
    """Fetch transaction amounts and deductible flags for a date range."""
    return db.session.query(
        Transaction.amount, Transaction.is_tax_deductible
    ).filter(
        Transaction.user_id == user_id,
        Transaction.date >= start_date,
        Transaction.date < end_date,
    ).all()


def calculate_monthly_summary(user_id, year, month):
    """Return (income, expenses) for a given month."""
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    start = datetime(year, month, 1)
    results = _get_tx_range(user_id, start, end)
    income = sum(r[0] for r in results if r[0] > 0)
    expenses = abs(sum(r[0] for r in results if r[0] < 0))
    return float(income), float(expenses)


def calculate_safe_to_spend(user_id, tax_rate):
    """
    A. The "Safe to Spend" Formula:
       Safe Balance = Bank Balance - Total Unsaved Tax Obligation

    Returns (balance, tax_obligation, safe_balance)
    """
    # Bank Balance = sum of all transactions (income - expenses)
    result = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
    ).scalar()
    balance = float(result or 0)

    # Calculate total tax obligation for the current year
    current_year = datetime.now().year
    tax_obligation = 0.0
    for q in range(1, 5):
        income, deductions = calculate_quarterly_income_deductions(user_id, q, current_year)
        net = income - deductions
        if net > 0:
            tax_obligation += net * tax_rate

    safe_balance = balance - tax_obligation
    return balance, tax_obligation, safe_balance


def calculate_quarterly_income_deductions(user_id, quarter, year):
    """
    Compute total income and total tax-deductible expenses for a quarter.
    Only expenses marked as is_tax_deductible reduce the tax burden.
    """
    start_date, end_date = get_quarter_date_range(quarter, year)
    results = _get_tx_range(user_id, start_date, end_date)
    income = sum(r[0] for r in results if r[0] > 0)
    deductions = sum(abs(r[0]) for r in results if r[0] < 0 and r[1])
    return float(income), float(deductions)


def calculate_quarterly_tax(user_id, quarter, year, tax_rate):
    """
    B. Quarterly Tax Estimator:
       Quarterly Net Income = Income - Deductible Expenses
       Estimated Tax = Quarterly Net Income * tax_rate

    Returns (income, deductions, net_income, estimated_tax)
    """
    income, deductions = calculate_quarterly_income_deductions(user_id, quarter, year)
    net_income = income - deductions
    estimated_tax = max(0, net_income * tax_rate)
    return float(income), float(deductions), float(net_income), float(estimated_tax)


def calculate_runway(user_id):
    """
    C. Runway Calculator:
       Average Monthly Expenses = Average of last 3 months of total expenses
       Runway (months) = Current Bank Balance / Average Monthly Expenses

    Returns (balance, avg_monthly_expenses, runway_months)
    """
    now = datetime.now()
    monthly_expenses = []
    for i in range(3):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        _, expenses = calculate_monthly_summary(user_id, year, month)
        monthly_expenses.append(expenses)

    avg_monthly_expenses = sum(monthly_expenses) / len(monthly_expenses) if monthly_expenses else 0
    result = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
    ).scalar()
    balance = float(result or 0)
    runway = balance / avg_monthly_expenses if avg_monthly_expenses > 0 else float('inf')
    return balance, avg_monthly_expenses, runway


def get_6_month_chart_data(user_id):
    """Return chart labels, income data, and expense data for the last 6 months."""
    now = datetime.now()
    labels = []
    income_data = []
    expense_data = []
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    for i in range(5, -1, -1):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        labels.append(f"{month_names[month - 1]} {year}")
        income, expenses = calculate_monthly_summary(user_id, year, month)
        income_data.append(income)
        expense_data.append(expenses)

    return labels, income_data, expense_data


def get_recent_transactions(user_id, limit=5):
    """Return the most recent transactions for a user."""
    return Transaction.query.filter_by(user_id=user_id)\
        .order_by(Transaction.date.desc())\
        .limit(limit).all()


def get_category_breakdown(user_id, year=None, month=None):
    """Return expense totals grouped by category for a given period."""
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    start = datetime(year, month, 1)

    results = db.session.query(
        Transaction.category, db.func.sum(Transaction.amount)
    ).filter(
        Transaction.user_id == user_id,
        Transaction.date >= start,
        Transaction.date < end,
        Transaction.amount < 0,
    ).group_by(Transaction.category).all()

    categories = []
    totals = []
    for cat, total in results:
        if cat:
            categories.append(cat)
            totals.append(abs(float(total)))

    return categories, totals
