from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, flash, abort, session
from flask_login import login_user, login_required, current_user
from .. import team, portal_auth
from ..models import AdminInvite, Business, User, db
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
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            flash(f'Welcome to {business_name}.', 'success')
            return redirect(url_for('dashboard.index'))

    return render_template('auth/setup.html')


@team_bp.route('/settings/team/invite', methods=['POST'])
@login_required
def invite():
    try:
        _, token = team.create_invite(request.form.get('email', ''), current_user.id)
    except ValueError as error:
        flash(str(error), 'error')
        return redirect(url_for('settings.index'))
    db.session.commit()

    # Handed back through the session for one render, the same way the client
    # invite is: the token exists nowhere else in a readable form, and the
    # session keeps it out of the URL, the history and the access log.
    session['admin_invite_url'] = url_for('team.join', token=token, _external=True)
    flash('Invitation created. Copy the link below and send it yourself.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/settings/team/invites/<int:id>/cancel', methods=['POST'])
@login_required
def cancel_invite(id):
    invite = db.session.get(AdminInvite, id)
    if invite and invite.is_open():
        team.cancel_invite(invite)
        db.session.commit()
        flash('Invitation cancelled.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/settings/team/admins/<int:id>/remove', methods=['POST'])
@login_required
def remove_admin(id):
    target = db.session.get(User, id)
    if not target:
        abort(404)
    allowed, reason = team.can_remove(target, current_user)
    if not allowed:
        flash(reason, 'error')
        return redirect(url_for('settings.index'))
    # The row goes; everything they created stays (its user_id now points at
    # nobody, which no query reads). No cascade, on purpose - ADR-0014.
    #
    # A plain `db.session.delete(target)` walks every relationship on User
    # (clients, projects, transactions, ...) and, absent a cascade, tries to
    # null out each child's user_id to disconnect it - which fails, since
    # those columns are NOT NULL. A bulk delete skips relationship
    # processing entirely and removes only the users row, which is what
    # ADR-0014 actually asks for.
    email = target.email
    User.query.filter_by(id=target.id).delete()
    db.session.commit()
    flash(f'{email} is no longer an admin.', 'success')
    return redirect(url_for('settings.index'))


@team_bp.route('/join/<token>', methods=['GET', 'POST'])
def join(token):
    invite = team.open_invite(token)
    if not invite:
        # One message for expired, already-used and never-existed alike.
        flash('That invitation link is no longer valid. Ask for a new one.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
            return render_template('auth/join.html', invite=invite, token=token)

        user, error = team.redeem_invite(invite, password)
        if error:
            db.session.rollback()
            flash(error, 'error')
            return render_template('auth/join.html', invite=invite, token=token)

        db.session.commit()
        portal_auth.forget_portal_session()
        login_user(user, remember=True)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        flash(f'Welcome to {Business.get().name}.', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template('auth/join.html', invite=invite, token=token)
