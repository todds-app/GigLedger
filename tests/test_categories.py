"""Editing and deleting categories in Settings.

Every category - defaults included - can be renamed or removed. The only
guard is on the last one: the custom list stores '' to mean "use the
defaults", so emptying it would silently bring all the defaults back.
"""
from datetime import datetime

from gigledger.models import (DEFAULT_EXPENSE_CATEGORIES, EXPENSE, INCOME,
                              Business, RecurringTransaction, Transaction, db)


def _add_transaction(kind, category, amount=-10.0):
    t = Transaction(user_id=1, date=datetime(2026, 9, 1), kind=kind,
                    category=category, amount=amount, description='fixture')
    db.session.add(t)
    db.session.commit()
    return t.id


def test_renaming_a_category_keeps_its_position(admin_client, app):
    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Software', 'new_name': 'Subscriptions'})
    with app.app_context():
        cats = Business.get().get_expense_categories()
        expected = ['Subscriptions' if c == 'Software' else c for c in DEFAULT_EXPENSE_CATEGORIES]
        assert cats == expected


def test_renaming_a_category_renames_it_on_existing_transactions(admin_client, app):
    with app.app_context():
        tid = _add_transaction(EXPENSE, 'Software')
        r = RecurringTransaction(user_id=1, description='Figma', amount=-15.0,
                                 kind=EXPENSE, category='Software')
        db.session.add(r)
        db.session.commit()
        rid = r.id

    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Software', 'new_name': 'Subscriptions'})

    with app.app_context():
        assert db.session.get(Transaction, tid).category == 'Subscriptions'
        assert db.session.get(RecurringTransaction, rid).category == 'Subscriptions'


def test_renaming_only_touches_transactions_of_that_kind(admin_client, app):
    """The same word can name an income category and an expense category."""
    with app.app_context():
        admin_client.post('/settings/categories/income/add', data={'category_name': 'Software'})
        income_id = _add_transaction(INCOME, 'Software', amount=100.0)
        expense_id = _add_transaction(EXPENSE, 'Software')

    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Software', 'new_name': 'Subscriptions'})

    with app.app_context():
        assert db.session.get(Transaction, expense_id).category == 'Subscriptions'
        assert db.session.get(Transaction, income_id).category == 'Software'
        assert 'Software' in Business.get().get_income_categories()


def test_renaming_to_an_existing_name_is_refused(admin_client, app):
    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Software', 'new_name': 'Internet'})
    with app.app_context():
        cats = Business.get().get_expense_categories()
        assert cats == DEFAULT_EXPENSE_CATEGORIES
        assert cats.count('Internet') == 1


def test_renaming_to_an_empty_name_is_refused(admin_client, app):
    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Software', 'new_name': '   '})
    with app.app_context():
        assert Business.get().get_expense_categories() == DEFAULT_EXPENSE_CATEGORIES


def test_a_default_category_can_be_deleted(admin_client, app):
    admin_client.post('/settings/categories/expense/delete', data={'category_name': 'Software'})
    with app.app_context():
        cats = Business.get().get_expense_categories()
        assert 'Software' not in cats
        assert len(cats) == len(DEFAULT_EXPENSE_CATEGORIES) - 1


def test_the_last_category_cannot_be_deleted(admin_client, app):
    with app.app_context():
        Business.get().custom_income_categories = 'Only One'
        db.session.commit()
    admin_client.post('/settings/categories/income/delete', data={'category_name': 'Only One'})
    with app.app_context():
        assert Business.get().get_income_categories() == ['Only One']


def test_settings_offers_edit_and_delete_on_every_category(admin_client):
    body = admin_client.get('/settings').get_data(as_text=True)
    for kind in ('income', 'expense', 'inventory'):
        assert f'/settings/categories/{kind}/rename' in body
    # Defaults used to be locked; now the delete form names them too.
    assert 'name="category_name" value="Software"' in body


def test_the_tax_reserve_category_is_locked_because_the_app_writes_it(admin_client, app):
    """Marking an invoice Paid files the reserve under this exact name."""
    admin_client.post('/settings/categories/expense/rename',
                      data={'category_name': 'Tax Reserve', 'new_name': 'Set Aside'})
    admin_client.post('/settings/categories/expense/delete', data={'category_name': 'Tax Reserve'})
    with app.app_context():
        assert 'Tax Reserve' in Business.get().get_expense_categories()
    body = admin_client.get('/settings').get_data(as_text=True)
    assert 'name="category_name" value="Tax Reserve"' not in body
