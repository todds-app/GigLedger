"""Characterization tests for the financial aggregations.

Written before the transaction-kind refactor and unchanged by it. Every number
here is the app's behaviour as it stood at commit f5427d2; the refactor's whole
promise is that none of them move. A failure in this file after a conversion
task means the conversion changed behaviour, not that the expectation is stale -
do not edit the expected values to make it pass.

The ledger fixture is built by hand rather than from the demo seed, which uses
`random` and so cannot pin an exact figure.
"""
import io
from datetime import datetime

import pytest

import gigledger.app
from gigledger.app import create_app
from gigledger.models import Transaction, User, db


def build_app(tmp_path, monkeypatch, **config):
    """An app backed by a throwaway database, never the real one."""
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key',
                      WTF_CSRF_ENABLED=False, **config)
    app.login_manager.session_protection = None
    return app


# (amount, category, is_tax_deductible, day) - all in March 2026, Q1.
LEDGER = [
    (5000.00, 'Client Payment', False, 3),
    (2500.00, 'Consulting', False, 10),
    (-1200.00, 'Software', True, 5),
    (-300.00, 'Travel', True, 12),
    (-450.00, 'Meal', False, 18),
]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """An app whose only transactions are LEDGER, owned by the demo user."""
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        user = User.query.filter_by(email='demo@gigledger.com').first()
        Transaction.query.delete()
        for amount, category, deductible, day in LEDGER:
            db.session.add(Transaction(
                user_id=user.id, amount=amount,
                date=datetime(2026, 3, day, 12, 0),
                category=category, description='fixture',
                is_tax_deductible=deductible, source='manual'))
        db.session.commit()
    return app


def demo_user_id(app):
    with app.app_context():
        return User.query.filter_by(email='demo@gigledger.com').first().id


def authenticated_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(demo_user_id(app))
        session['_fresh'] = True
    return client


def test_monthly_summary(ledger):
    from gigledger.finance import calculate_monthly_summary
    with ledger.app_context():
        income, expenses = calculate_monthly_summary(demo_user_id(ledger), 2026, 3)
    assert income == 7500.00
    assert expenses == 1950.00


def test_quarterly_income_and_deductions(ledger):
    from gigledger.finance import calculate_quarterly_income_deductions
    with ledger.app_context():
        income, deductions = calculate_quarterly_income_deductions(
            demo_user_id(ledger), 1, 2026)
    assert income == 7500.00
    assert deductions == 1500.00


def test_quarterly_tax(ledger):
    from gigledger.finance import calculate_quarterly_tax
    with ledger.app_context():
        income, deductions, net, tax = calculate_quarterly_tax(
            demo_user_id(ledger), 1, 2026, 0.30)
    assert (income, deductions, net) == (7500.00, 1500.00, 6000.00)
    assert tax == pytest.approx(1800.00)


def test_bank_balance_is_every_transaction(ledger):
    """Cash, not profit: the balance sums all transactions regardless of kind."""
    from gigledger.finance import calculate_safe_to_spend
    with ledger.app_context():
        balance, _, _ = calculate_safe_to_spend(demo_user_id(ledger), 0.30)
    assert balance == pytest.approx(5550.00)


def test_category_breakdown_is_expenses_only(ledger):
    from gigledger.finance import get_category_breakdown
    with ledger.app_context():
        categories, totals = get_category_breakdown(
            demo_user_id(ledger), year=2026, month=3)
    assert dict(zip(categories, totals)) == {
        'Software': 1200.00, 'Travel': 300.00, 'Meal': 450.00}


def test_csv_export_rows_and_summary(ledger):
    body = authenticated_client(ledger).get(
        '/transactions/export/csv').get_data(as_text=True)
    assert 'Client Payment' in body
    assert ',Income,' in body
    assert ',Expense,' in body
    assert 'Total Income,,,,7500.00' in body
    assert 'Total Expenses,,,,1950.00' in body
    assert 'Deductible Expenses,,,,1500.00' in body
    assert 'Net,,,,5550.00' in body


def test_csv_export_type_filter(ledger):
    client = authenticated_client(ledger)
    income_only = client.get(
        '/transactions/export/csv?type=income').get_data(as_text=True)
    assert 'Total Income,,,,7500.00' in income_only
    assert 'Total Expenses,,,,0.00' in income_only

    expense_only = client.get(
        '/transactions/export/csv?type=expense').get_data(as_text=True)
    assert 'Total Income,,,,0.00' in expense_only
    assert 'Total Expenses,,,,1950.00' in expense_only


def test_pdf_export_totals(ledger):
    body = authenticated_client(ledger).get(
        '/transactions/export/pdf').get_data(as_text=True)
    assert '7,500.00' in body
    assert '1,950.00' in body


def test_transactions_list_type_filter(ledger):
    client = authenticated_client(ledger)
    assert client.get('/transactions?type=income').status_code == 200
    assert b'2 transaction' in client.get('/transactions?type=income').data
    assert b'3 transaction' in client.get('/transactions?type=expense').data


def test_reports_year_totals(ledger):
    body = authenticated_client(ledger).get(
        '/reports?year=2026').get_data(as_text=True)
    assert '7,500.00' in body
    assert '1,950.00' in body


def test_dashboard_renders(ledger):
    assert authenticated_client(ledger).get('/').status_code == 200
