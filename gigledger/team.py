"""
GigLedger - Admin accounts: who may be one, and how they get in.

One install keeps books for one business, and every admin sees every row
(docs/adr/0014). Admins are therefore added by invitation from Settings rather
than by public sign-up, and a fresh install creates its first admin through a
setup screen that exists only while there are no admins at all.

The invite mechanics deliberately mirror portal_auth: the token is returned
exactly once and stored only hashed.
"""
import secrets
from datetime import datetime

from . import portal_auth
from .app import bcrypt
from .models import AdminInvite, User, db


def needs_setup():
    """True only while the install has no admin at all. The setup route uses
    this to exist, and the login route to redirect; it must never be true on
    a working install, which is what makes /setup safe to leave registered."""
    return User.query.count() == 0


def create_invite(email, invited_by):
    """Open an invite. Returns (invite, token); the token is returned exactly
    once, here. Raises ValueError if the email already belongs to an admin -
    refused at creation so the inviter sees it, not the invitee at redeem.

    Reissuing closes any invite still open for the same email: two live
    invites would mean cancelling one does not cancel access.
    """
    email = (email or '').strip().lower()
    if not email:
        raise ValueError('An email address is needed.')
    if User.query.filter_by(email=email).first():
        raise ValueError(f'{email} is already an admin.')

    for previous in AdminInvite.query.filter_by(email=email).all():
        if previous.is_open():
            previous.expires_at = datetime.utcnow()

    token = secrets.token_urlsafe(32)
    invite = AdminInvite(email=email, invited_by=invited_by,
                         token_hash=portal_auth.hash_token(token),
                         expires_at=datetime.utcnow() + portal_auth.INVITE_TTL)
    db.session.add(invite)
    return invite, token


def open_invite(token):
    invite = AdminInvite.query.filter_by(token_hash=portal_auth.hash_token(token)).first()
    return invite if invite and invite.is_open() else None


def cancel_invite(invite):
    invite.expires_at = datetime.utcnow()


def redeem_invite(invite, password):
    """Turn an open invite into an admin. Returns (user, error)."""
    if len(password or '') < portal_auth.MIN_PASSWORD_LENGTH:
        return None, f'Choose a password of at least {portal_auth.MIN_PASSWORD_LENGTH} characters.'
    if User.query.filter_by(email=invite.email).first():
        # Became an admin some other way since the invite was issued.
        return None, 'That invitation is no longer valid.'
    user = User(email=invite.email,
                password_hash=bcrypt.generate_password_hash(password).decode('utf-8'))
    db.session.add(user)
    invite.redeemed_at = datetime.utcnow()
    return user, None


def can_remove(target, actor):
    """(allowed, reason). Two refusals: yourself, and the last admin standing -
    either would lock the install."""
    if target.id == actor.id:
        return False, 'You cannot remove your own account.'
    if User.query.count() <= 1:
        return False, 'The last admin cannot be removed.'
    return True, ''
