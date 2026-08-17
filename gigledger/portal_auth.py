"""
GigLedger - Client Portal authentication.

The portal deliberately does not use Flask-Login. See docs/adr/0008 for the
full argument; the short version is that `current_user` is assumed to be a
freelancer by roughly forty existing routes, all of which filter rows with
`user_id=current_user.id`. If a client could become `current_user`, a portal
account with id 3 would be served freelancer #3's data. That is not a missing
check to add - it is a type confusion, and the fix is to make the two principals
incapable of being mistaken for one another.

So: freelancers live in Flask-Login's session keys and clients live in
`portal_account_id`, and the two are mutually exclusive. `@login_required` keeps
meaning exactly what every existing route already assumes it means.
"""
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import g, redirect, request, session, url_for
from flask_login import logout_user

from .app import bcrypt
from .models import Client, LoginAttempt, PortalAccount, PortalInvite, db

SESSION_ACCOUNT_KEY = 'portal_account_id'
SESSION_EPOCH_KEY = 'portal_epoch'

INVITE_TTL = timedelta(days=7)
MIN_PASSWORD_LENGTH = 12

# Throttling. Durable in SQLite rather than in memory: a restart must not be a
# way to clear the counter, and more than one worker process must share it.
MAX_FAILURES = 5
LOCKOUT_WINDOW = timedelta(minutes=15)


def is_enabled():
    """The portal is on unless an operator turns it off. Disabling skips
    blueprint registration entirely, so the routes 404 rather than refuse - a
    route that exists and refuses is still a surface and still announces the
    feature."""
    return os.environ.get('PORTAL_ENABLED', '1').lower() not in ('0', 'false', 'no')


# --- passwords and tokens ------------------------------------------------

def set_password(account, password):
    account.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')


def check_password(account, password):
    return bool(account) and bcrypt.check_password_hash(account.password_hash, password)


def hash_token(token):
    """SHA-256, not bcrypt. This is a 256-bit random token, not a password:
    there is no dictionary to attack, so the only property needed is that the
    stored form is not usable as the token."""
    return hashlib.sha256(token.encode()).hexdigest()


# --- invites -------------------------------------------------------------

def create_invite(client, email):
    """Open an invite for a client. Returns (invite, token).

    The token is returned exactly once, here. Nothing stores it in a form that
    can be read back, so a lost invite is reissued rather than recovered.

    Reissuing closes any invite still open for this client: two live invites
    would mean revoking one does not revoke access.
    """
    for previous in client.portal_invites:
        if previous.is_open():
            previous.expires_at = datetime.utcnow()

    token = secrets.token_urlsafe(32)
    invite = PortalInvite(client_id=client.id,
                          email=(email or client.email or '').strip().lower(),
                          token_hash=hash_token(token),
                          expires_at=datetime.utcnow() + INVITE_TTL)
    db.session.add(invite)
    return invite, token


def open_invite(token):
    invite = PortalInvite.query.filter_by(token_hash=hash_token(token)).first()
    return invite if invite and invite.is_open() else None


def redeem_invite(invite, password):
    """Turn an open invite into portal access. Returns (account, error).

    Two paths, and the difference between them is the security-relevant part:

    * No account for this email yet - create one with the given password.
    * An account already exists - the given password must be **that account's**,
      because linking is granting the account access to another freelancer's
      documents. Whoever holds the invite must prove they control the account,
      or an invite would be enough to claim someone else's identity.
    """
    client = db.session.get(Client, invite.client_id)
    account = PortalAccount.query.filter_by(email=invite.email).first()

    if account is None:
        if len(password or '') < MIN_PASSWORD_LENGTH:
            return None, f'Choose a password of at least {MIN_PASSWORD_LENGTH} characters.'
        account = PortalAccount(email=invite.email)
        set_password(account, password)
        db.session.add(account)
        db.session.flush()
    elif not check_password(account, password):
        return None, ('An account already exists for that email. Enter its '
                      'existing password to link this project.')

    client.portal_account_id = account.id
    invite.redeemed_at = datetime.utcnow()
    return account, None


def revoke(client):
    """Withdraw a client's portal access, immediately.

    Bumping the epoch is what makes it immediate: sessions carry the value they
    saw at login and are checked on every request. Unlinking alone would leave
    an already-issued cookie working until it lapsed.
    """
    account = client.portal_account
    client.portal_account_id = None
    for invite in client.portal_invites:
        if invite.is_open():
            invite.expires_at = datetime.utcnow()
    if account:
        account.session_epoch = (account.session_epoch or 1) + 1


# --- throttling ----------------------------------------------------------

def _recent_failures(scope, identifier):
    since = datetime.utcnow() - LOCKOUT_WINDOW
    return LoginAttempt.query.filter(
        LoginAttempt.scope == scope,
        LoginAttempt.identifier == (identifier or '').lower(),
        LoginAttempt.at >= since).count()


def is_locked_out(scope, identifier):
    return _recent_failures(scope, identifier) >= MAX_FAILURES


def record_failure(scope, identifier):
    db.session.add(LoginAttempt(scope=scope, identifier=(identifier or '').lower(),
                                ip=(request.remote_addr or '')[:64]))
    db.session.commit()


def clear_failures(scope, identifier):
    LoginAttempt.query.filter_by(scope=scope,
                                 identifier=(identifier or '').lower()).delete()
    db.session.commit()


# --- sessions ------------------------------------------------------------

def log_in(account):
    # Never two principals in one session: that is a state in which a
    # decorator's ordering decides who you are.
    logout_user()
    session.pop('_user_id', None)
    session[SESSION_ACCOUNT_KEY] = account.id
    session[SESSION_EPOCH_KEY] = account.session_epoch
    account.last_login_at = datetime.utcnow()
    db.session.commit()


def log_out():
    session.pop(SESSION_ACCOUNT_KEY, None)
    session.pop(SESSION_EPOCH_KEY, None)


def forget_portal_session():
    """Called when a freelancer logs in, for the same reason log_in() calls
    logout_user()."""
    log_out()


def current_account():
    """The portal account for this request, or None.

    The epoch comparison happens here rather than at login, so revocation takes
    effect on the next request rather than the next login.
    """
    account_id = session.get(SESSION_ACCOUNT_KEY)
    if not account_id:
        return None

    account = db.session.get(PortalAccount, account_id)
    if not account or session.get(SESSION_EPOCH_KEY) != account.session_epoch:
        log_out()
        return None
    return account


def visible_clients(account):
    """The Client rows this account may act through.

    Every portal query starts here. PortalAccount is the schema's one
    cross-tenant object; this function is the seam where a request drops back
    into tenant-scoped data, and nothing in the portal should reach around it.
    """
    return Client.query.filter_by(portal_account_id=account.id).all()


def client_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if not account:
            return redirect(url_for('portal.login'))
        g.portal_account = account
        return view(*args, **kwargs)
    return wrapped
