from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_user
from .. import team, portal_auth
from ..models import Business, User, db
from ..app import bcrypt

team_bp = Blueprint('team', __name__)


@team_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """First run only. 404 - not a refusal - once any admin exists, so the
    route cannot later be used to add an account (ADR-0014)."""
    if not team.needs_setup():
        abort(404)

    if request.method == 'POST':
        business_name = request.form.get('business_name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not business_name or not email:
            flash('A business name and an email address are needed.', 'error')
        elif password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
        elif len(password) < portal_auth.MIN_PASSWORD_LENGTH:
            flash(f'Choose a password of at least {portal_auth.MIN_PASSWORD_LENGTH} characters.', 'error')
        else:
            business = Business.get()
            business.name = business_name
            user = User(email=email,
                        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'))
            db.session.add(user)
            db.session.commit()
            portal_auth.forget_portal_session()
            login_user(user, remember=True)
            flash(f'Welcome to {business_name}.', 'success')
            return redirect(url_for('dashboard.index'))

    return render_template('auth/setup.html')
