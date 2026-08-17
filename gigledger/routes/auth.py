from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from .. import portal_auth
from ..models import User, db
from ..app import bcrypt

auth_bp = Blueprint('auth', __name__)

APP_SCOPE = 'app'


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        # The same throttle the portal uses. Hardening the client-facing login
        # while leaving this one unlimited is not a position worth defending,
        # and it is the same helper either way. See docs/adr/0008.
        if portal_auth.is_locked_out(APP_SCOPE, email):
            flash('Too many failed attempts. Try again in a few minutes.', 'error')
            return render_template('auth/login.html')

        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            portal_auth.clear_failures(APP_SCOPE, email)
            # A freelancer session and a portal session are never both present:
            # that is a state in which a decorator's ordering decides who you are.
            portal_auth.forget_portal_session()
            login_user(user, remember=True)
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard.index'))
        else:
            portal_auth.record_failure(APP_SCOPE, email)
            flash('Invalid email or password.', 'error')

    return render_template('auth/login.html')


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        tax_rate = request.form.get('tax_rate', '30')

        if not email or not password:
            flash('Email and password are required.', 'error')
        elif password != confirm_password:
            flash('Passwords do not match.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('An account with this email already exists.', 'error')
        else:
            try: tax_rate_val = float(tax_rate) / 100.0
            except ValueError: tax_rate_val = 0.30

            password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
            user = User(email=email, password_hash=password_hash, default_tax_rate=tax_rate_val)
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=True)
            flash('Account created! Welcome to GigLedger.', 'success')
            return redirect(url_for('dashboard.index'))

    return render_template('auth/signup.html')


# POST only: a GET that mutates session state is reachable by <img src="/logout">
# and is not covered by CSRF protection, which only guards unsafe methods.
@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
