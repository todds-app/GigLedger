from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from .. import portal_auth, team
from ..models import User, db
from ..app import bcrypt

auth_bp = Blueprint('auth', __name__)

APP_SCOPE = 'app'


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if team.needs_setup():
        return redirect(url_for('team.setup'))

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
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            flash('Welcome back!', 'success')
            return redirect(url_for('dashboard.index'))
        else:
            portal_auth.record_failure(APP_SCOPE, email)
            flash('Invalid email or password.', 'error')

    return render_template('auth/login.html')


# POST only: a GET that mutates session state is reachable by <img src="/logout">
# and is not covered by CSRF protection, which only guards unsafe methods.
@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
