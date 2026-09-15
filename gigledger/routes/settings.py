from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user
from ..models import (db, AdminInvite, Business, User, DEFAULT_INCOME_CATEGORIES,
                      DEFAULT_EXPENSE_CATEGORIES, DEFAULT_INVENTORY_CATEGORIES)

settings_bp = Blueprint('settings', __name__)

AVAILABLE_THEMES = {
    'emerald': {'name': 'Emerald', 'desc': 'Fresh green, the default'},
    'ocean': {'name': 'Ocean Blue', 'desc': 'Deep and professional'},
    'sunset': {'name': 'Sunset Orange', 'desc': 'Warm and energetic'},
    'rose': {'name': 'Rose Pink', 'desc': 'Bold and vibrant'},
    'midnight': {'name': 'Midnight Slate', 'desc': 'Sleek and sophisticated'},
}


@settings_bp.route('/settings')
@login_required
def index():
    business = Business.get()
    return render_template('settings/index.html', user=current_user,
        tax_rate_percent=int(business.default_tax_rate * 100),
        income_categories=business.get_income_categories(),
        expense_categories=business.get_expense_categories(),
        default_income_categories=DEFAULT_INCOME_CATEGORIES,
        default_expense_categories=DEFAULT_EXPENSE_CATEGORIES,
        inventory_categories=business.get_inventory_categories(),
        default_inventory_categories=DEFAULT_INVENTORY_CATEGORIES,
        available_themes=AVAILABLE_THEMES,
        current_theme=current_user.theme or 'emerald',
        dark_mode=current_user.dark_mode or False,
        admins=User.query.order_by(User.created_at).all(),
        open_invites=[i for i in AdminInvite.query.order_by(AdminInvite.created_at).all()
                      if i.is_open()],
        invite_url=session.pop('admin_invite_url', None))


@settings_bp.route('/settings/tax-rate', methods=['POST'])
@login_required
def update_tax_rate():
    try:
        tax_rate = float(request.form.get('tax_rate', '30'))
        if tax_rate < 0 or tax_rate > 100: raise ValueError
        Business.get().default_tax_rate = tax_rate / 100.0
        db.session.commit()
        flash(f'Tax rate updated to {tax_rate}%. All calculations now use this rate.', 'success')
    except ValueError:
        flash('Please enter a valid tax rate between 0 and 100.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/currency', methods=['POST'])
@login_required
def update_currency():
    currency = request.form.get('currency', 'USD')
    if currency in ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'INR', 'JPY']:
        Business.get().currency = currency
        db.session.commit()
        flash(f'Currency updated to {currency}.', 'success')
    else:
        flash('Unsupported currency.', 'error')
    return redirect(url_for('settings.index'))


# One list per kind. The routes stay separate endpoints - templates and the
# CSRF walk name them - but the behaviour is written once.
CATEGORY_LISTS = {
    'income': ('Income', 'custom_income_categories', DEFAULT_INCOME_CATEGORIES),
    'expense': ('Expense', 'custom_expense_categories', DEFAULT_EXPENSE_CATEGORIES),
    'inventory': ('Inventory', 'custom_inventory_categories', DEFAULT_INVENTORY_CATEGORIES),
}


def _current(kind):
    business = Business.get()
    return {'income': business.get_income_categories,
            'expense': business.get_expense_categories,
            'inventory': business.get_inventory_categories}[kind]()


def _add_category(kind):
    label, column, _ = CATEGORY_LISTS[kind]
    name = request.form.get('category_name', '').strip()
    if not name:
        flash('Category name cannot be empty.', 'error')
    elif name in _current(kind):
        flash(f'Category "{name}" already exists.', 'error')
    else:
        setattr(Business.get(), column, ','.join(_current(kind) + [name]))
        db.session.commit()
        flash(f'{label} category "{name}" added!', 'success')
    return redirect(url_for('settings.index'))


def _delete_category(kind):
    label, column, defaults = CATEGORY_LISTS[kind]
    name = request.form.get('category_name', '').strip()
    cats = _current(kind)
    if name in defaults:
        # The UI offers no button for these; the route agrees.
        flash('Default categories cannot be removed.', 'error')
    elif name in cats:
        cats.remove(name)
        setattr(Business.get(), column, ','.join(cats) if cats else '')
        db.session.commit()
        flash(f'{label} category "{name}" removed.', 'success')
    else:
        flash(f'Category "{name}" not found.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/categories/income/add', methods=['POST'])
@login_required
def add_income_category():
    return _add_category('income')


@settings_bp.route('/settings/categories/income/delete', methods=['POST'])
@login_required
def delete_income_category():
    return _delete_category('income')


@settings_bp.route('/settings/categories/expense/add', methods=['POST'])
@login_required
def add_expense_category():
    return _add_category('expense')


@settings_bp.route('/settings/categories/expense/delete', methods=['POST'])
@login_required
def delete_expense_category():
    return _delete_category('expense')


@settings_bp.route('/settings/categories/inventory/add', methods=['POST'])
@login_required
def add_inventory_category():
    return _add_category('inventory')


@settings_bp.route('/settings/categories/inventory/delete', methods=['POST'])
@login_required
def delete_inventory_category():
    return _delete_category('inventory')


@settings_bp.route('/settings/theme', methods=['POST'])
@login_required
def update_theme():
    theme = request.form.get('theme', 'emerald')
    if theme in AVAILABLE_THEMES:
        current_user.theme = theme
        db.session.commit()
        flash(f'Theme changed to {AVAILABLE_THEMES[theme]["name"]}.', 'success')
    else:
        flash('Invalid theme selection.', 'error')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/dark-mode', methods=['POST'])
@login_required
def toggle_dark_mode():
    dark = request.form.get('dark_mode', 'off')
    current_user.dark_mode = (dark == 'on')
    db.session.commit()
    flash('Dark mode ' + ('enabled' if current_user.dark_mode else 'disabled') + '.', 'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/categories/reset', methods=['POST'])
@login_required
def reset_categories():
    business = Business.get()
    business.custom_income_categories = ''
    business.custom_expense_categories = ''
    business.custom_inventory_categories = ''
    db.session.commit()
    flash('Categories reset to defaults.', 'success')
    return redirect(url_for('settings.index'))


@settings_bp.route('/settings/business', methods=['POST'])
@login_required
def update_business():
    business = Business.get()
    business.name = request.form.get('business_name', '').strip()
    business.address = request.form.get('business_address', '').strip()
    business.phone = request.form.get('business_phone', '').strip()
    business.invoice_note = request.form.get('invoice_note', '').strip()
    business.invoice_prefix = request.form.get('invoice_prefix', 'INV').strip()
    db.session.commit()
    flash('Business profile updated!', 'success')
    return redirect(url_for('settings.index'))
