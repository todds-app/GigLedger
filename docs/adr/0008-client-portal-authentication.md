# 0008. Authenticate portal clients outside Flask-Login, in their own session

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The Client Portal needed clients to log in. A `Client` had never been a
principal: it is a row a freelancer typed in, with a `name` and an `email` and
nothing identity-like. Every other row in the schema is scoped by `user_id`, and
a `User` is a freelancer.

The obvious implementation is to give clients credentials and load them through
the existing `login_manager` with namespaced session ids — `"u:1"` for a user,
`"c:1"` for a client. **That implementation has a cross-tenant data leak in it,
and it is not a missing check.**

Roughly forty existing routes are written like this:

```python
Transaction.query.filter_by(user_id=current_user.id)
```

They are correct because `current_user` is always a freelancer. Make
`current_user` sometimes a client and the expression stays syntactically fine
while changing meaning: a client with primary key 3 requesting `/transactions`
satisfies `@login_required`, reaches the handler, and is served **freelancer
#3's transactions**. The same for invoices, projects, goals, and settings. It is
a type confusion, and the mitigation — adding an "is this actually a freelancer"
assertion to all forty routes — converts a new feature into an audit of existing
code, where the cost of one omission is another tenant's financial records.

Two further facts shaped the rest of the decision:

- **This app cannot send email.** No SMTP configuration, no mail dependency,
  nothing. Any invite or magic-link flow that assumes outbound mail is a flow
  that fails silently in a self-hosted deployment.
- **A client email is not unique to a freelancer.** `billing@acme.com` may
  legitimately be a client of several GigLedger users, so an email cannot
  identify a row in `clients` at a login form.

## Decision

**Clients do not touch Flask-Login.** Portal identity lives in its own session
key, `portal_account_id`, behind a `@client_required` decorator.
`@login_required` and `current_user` keep meaning *freelancer*, exactly as all
existing code already assumes, and no portal session can satisfy them. The
failure mode above becomes impossible rather than tested-against.

**The two sessions are mutually exclusive.** Logging into the portal clears the
freelancer session and vice versa, so there is no request where both principals
are present and a decorator's ordering decides who you are.

**`PortalAccount` is global, keyed by email**, and linked many-to-one from
tenant-scoped `Client` rows. This is the schema's first deliberately
cross-tenant object. It holds credentials only — never documents, never project
data — and `visible_clients()` is the single seam through which a portal request
drops back into tenant-scoped data.

**Access is granted by invitation, delivered by the freelancer.** The app
generates a `secrets.token_urlsafe(32)`, stores only its SHA-256, and shows the
URL once; the freelancer sends it however they already talk to that client. No
SMTP, and the person who decides who gets access is the person who already knows
who the client is.

**Redeeming an invite onto an existing account requires that account's
password.** Linking grants an existing identity access to another freelancer's
material, so whoever holds the invite must prove they control the account.
Without this, holding an invite would be enough to claim someone else's email.

**Revocation is immediate, via a session epoch.** `PortalAccount.session_epoch`
is stamped into the session at login and compared on every request; revoking or
reissuing bumps it. Unlinking alone would leave an issued cookie working until
it lapsed, which is not what "revoke" means to the person clicking it.

**Failed logins are throttled durably**, in a `login_attempts` table rather than
in memory, so a restart does not clear the counter and multiple workers share
it. Applied to the freelancer login as well: hardening the new door while
leaving the old one unlimited is not a defensible position, and it is the same
helper either way.

**The portal can be switched off** with `PORTAL_ENABLED=0`, which skips
blueprint registration so the routes 404. A route that exists and refuses is
still a surface and still announces the feature.

## Consequences

- The portal gets its own base template with no freelancer navigation, so there
  is no `{% if %}` whose omission renders a link into the freelancer's app —
  the same reasoning [ADR-0005](0005-render-documents-from-templates.md) applied
  to the export documents.
- **A lost invite is reissued, never recovered.** Only the hash is stored. The
  URL is passed to the page that displays it through the session rather than the
  query string, so it stays out of browser history and access logs.
- Portal passwords have a 12-character minimum, against the freelancer login's
  6. The asymmetry is deliberate — portal credentials are held by third parties
  who did not choose this software — and it means the existing signup rule is
  now the weakest in the app. Worth raising separately.
- **`test_the_probe_is_not_vacuous` matters more here than it did for CSRF.**
  The route-walking guard test was verified by mutation: removing
  `@client_required` from `portal.index` makes it fail with
  `[('portal.index', 200)]`. An early version of the probe reused one client
  across all rules, so probing `/portal/logout` signed the probe out and made
  every route examined afterwards look guarded — it passed for the wrong reason
  until each rule got a fresh client.
- `PortalAccount` being global means a person with one email sees every
  freelancer who shared with them behind one login. That is the intent, but it
  makes the portal's grouping and labelling a correctness concern, not a
  cosmetic one: material from two freelancers must never render as one list.
- The throttle counts failures per identifier within a window and clears them on
  success. It does not defend against a distributed attempt spread across many
  identifiers, and it does not rate-limit invite redemption by IP. Both are
  acceptable at this scale and neither is addressed here.
- `clients.portal_account_id` is the one new column on an existing table, added
  through the hand-rolled `_migrate_db()`. That function is now the app's next
  piece of infrastructure debt: it has no down-migrations and no record of what
  ran, so the first change that needs to *modify* rather than *add* a column is
  the one it cannot express. Adopting Alembic deserves its own decision rather
  than riding along with a feature.
