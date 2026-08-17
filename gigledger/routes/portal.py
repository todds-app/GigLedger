"""
GigLedger - Client Portal Blueprint.

Registered only when PORTAL_ENABLED is on, so a deployment that does not want an
internet-facing client login does not merely refuse those routes - it does not
have them. See docs/adr/0008.

Every view here is @client_required except login and invite redemption, which
cannot be: you cannot log in from inside a session you do not have.
tests/test_portal_auth.py walks the url_map and fails on any other exception.
"""
from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   g)

from .. import portal_auth
from ..portal_auth import client_required
from ..models import PortalAccount, db

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

SCOPE = 'portal'


@portal_bp.route('/login', methods=['GET', 'POST'])
def login():
    if portal_auth.current_account():
        return redirect(url_for('portal.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        # Checked before the password is, so a locked-out identifier costs an
        # attacker a lookup rather than a hash comparison.
        if portal_auth.is_locked_out(SCOPE, email):
            flash('Too many failed attempts. Try again in a few minutes.', 'error')
            return render_template('portal/login.html')

        account = PortalAccount.query.filter_by(email=email).first()
        if portal_auth.check_password(account, password):
            portal_auth.clear_failures(SCOPE, email)
            portal_auth.log_in(account)
            return redirect(url_for('portal.index'))

        portal_auth.record_failure(SCOPE, email)
        flash('Invalid email or password.', 'error')

    return render_template('portal/login.html')


@portal_bp.route('/logout', methods=['POST'])
@client_required
def logout():
    portal_auth.log_out()
    flash('You have been signed out.', 'info')
    return redirect(url_for('portal.login'))


@portal_bp.route('/invite/<token>', methods=['GET', 'POST'])
def redeem(token):
    invite = portal_auth.open_invite(token)
    if not invite:
        # One message for expired, already-used and never-existed alike: the
        # difference between them is not the visitor's business.
        flash('That invitation link is no longer valid. Ask for a new one.', 'error')
        return redirect(url_for('portal.login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
            return render_template('portal/redeem.html', invite=invite, token=token)

        account, error = portal_auth.redeem_invite(invite, password)
        if error:
            db.session.rollback()
            flash(error, 'error')
            return render_template('portal/redeem.html', invite=invite, token=token)

        db.session.commit()
        portal_auth.log_in(account)
        flash('Your portal access is set up.', 'success')
        return redirect(url_for('portal.index'))

    return render_template('portal/redeem.html', invite=invite, token=token)


@portal_bp.route('/')
@client_required
def index():
    return render_template('portal/index.html',
                           account=g.portal_account,
                           clients=portal_auth.visible_clients(g.portal_account))
