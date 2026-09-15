"""
GigLedger - Admin accounts: who may be one, and how they get in.

One install keeps books for one business, and every admin sees every row
(docs/adr/0014). Admins are therefore added by invitation from Settings rather
than by public sign-up, and a fresh install creates its first admin through a
setup screen that exists only while there are no admins at all.

The invite mechanics deliberately mirror portal_auth: the token is returned
exactly once and stored only hashed.
"""
from .models import User


def needs_setup():
    """True only while the install has no admin at all. The setup route uses
    this to exist, and the login route to redirect; it must never be true on
    a working install, which is what makes /setup safe to leave registered."""
    return User.query.count() == 0
