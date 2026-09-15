# One business per install

- **Date:** 2026-09-15
- **Status:** Implemented 2026-09-15
- **Scope:** Every admin login sees and edits the same clients, projects,
  invoices, transactions, documents and settings. Admins are added by
  invitation from Settings; public sign-up is removed; a fresh install starts
  with a first-run setup screen.
- **Supersedes in part:** the per-user scoping premise of ADR-0008. The
  portal-vs-Flask-Login separation in that ADR stands unchanged.

## Problem

GigLedger was written for a freelancer working alone: every row carries a
`user_id`, and ~60 query sites across every route module filter with
`user_id=current_user.id`. Two logins are two businesses that cannot see each
other's data.

The install at ledger.danismithdesign.com is one interior-design practice — a
designer and, at most, an assistant or two — and every one of them needs the
same view. The choice made in brainstorming, and recorded here, is that this
install hosts **one business, full stop**, rather than businesses with teams.
Multi-tenancy is not being designed for; it is being removed.

Two consequences follow directly and are in scope:

- Public sign-up must go, or a stranger who registers would see the books.
- Settings that are business-wide (name, tax rate, invoice numbering,
  categories) can no longer live on the `User` row, or two admins would issue
  invoices from two counters.

## Decisions

### One `Business` row holds what is business-wide

```python
class Business(db.Model):
    __tablename__ = 'business'
    id                          = Integer, primary key
    name                        = String(200), default ''
    address                     = Text, default ''
    phone                       = String(50), default ''
    default_tax_rate            = Float, default 0.30
    currency                    = String(3), default 'USD'
    invoice_prefix              = String(10), default 'INV'
    next_invoice_number         = Integer, default 1
    invoice_note                = Text, default 'Thank you for your business!'
    custom_income_categories    = Text, default ''   # comma-separated, as today
    custom_expense_categories   = Text, default ''
    custom_inventory_categories = Text, default ''
```

`Business.get()` is the single seam: it returns the one row, creating a default
one if the table is empty. No route or template ever asks *which* business.
`get_next_invoice_number()` and the `get_*_categories()` helpers move here from
`User` unchanged in behaviour.

`User` keeps only what is personal to a login:
`id, email, password_hash, theme, dark_mode, created_at, last_login_at`
(`last_login_at` is new, for the Team panel). The business columns stop being
declared on the model. They stay in the SQLite table — SQLite cannot drop a
column cleanly and an undeclared column costs nothing.

### `user_id` columns stay, meaning "created by"

`Transaction`, `Client`, `Project`, `Invoice`, `Goal`, `RecurringTransaction`,
`ProjectDocument`, `InventoryItem` and `DocumentAccess` keep their `user_id`
column. Writes continue to stamp `current_user.id` on new rows. Nothing reads
the column for visibility any more, and no UI shows it; it is a passive record
of who added a row, kept because it is already there and removing it would
touch every table for no gain. There is no cascade from `User` to any of these
tables today and there must not be one: removing an admin must not remove the
rows they entered.

Portal uploads (ADR-0013) stamp the owner's `user_id`; under one business that
is the lowest-id admin. The attribution the portal cares about is the `Client`,
which is unchanged.

### `@login_required` is the whole access rule

Every `filter_by(user_id=current_user.id, ...)` loses its `user_id` clause.
The `_owned_project()` / `_owned_document()` helpers in `routes/projects.py`
stay as functions — they are still where "found or 404" lives — but become
plain primary-key lookups. Reads of `current_user.<business field>` become
reads of `business.<field>`, where `business` is `Business.get()` — fetched
once per request in the existing `app.context_processor` and injected into
every template, so templates never fetch it themselves.

The security story changes from *every query is scoped, so a bug cannot leak
across users* to *login required, then everything*. That is the correct shape
for an app whose every admin is meant to see everything. The Client Portal's
explicit-grant rule (ADR-0009) is untouched and is now the only place in the
app where two principals may see different rows.

### Admins are added by invitation, from Settings

```python
class AdminInvite(db.Model):
    __tablename__ = 'admin_invites'
    id          = Integer, primary key
    email       = String(200), not null      # lower-cased
    token_hash  = String(64), not null, indexed
    invited_by  = FK users.id, nullable
    expires_at  = DateTime, not null          # created_at + 7 days
    redeemed_at = DateTime, nullable
    created_at  = DateTime
```

A deliberate copy of `PortalInvite`, with the same rules: the token is
returned exactly once to the person who created it, stored only as a SHA-256
hash (`portal_auth.hash_token`), and reissuing to an email closes any invite
still open for it. An invite is refused at creation if the email already
belongs to an admin.

`GET /join/<token>` shows *"You have been invited to join {business.name}"*
and a set-password form (minimum length as for portal accounts).
`POST /join/<token>` creates the `User`, marks the invite redeemed and signs
the new admin in. An expired, redeemed or unknown token gets one and the same
"this invitation is not valid" page — which of the three it is would itself be
information. The route is not throttled: like the portal's redeem route, the
token is a 256-bit random value, not a guessable secret.

`/signup` and its template are deleted. The login page loses its "Sign up"
link.

### Team panel

A new section on the Settings page:

- **Admins** — email, joined date, last sign-in. Each row has *Remove*,
  disabled for the current user and for the last remaining admin. Removing
  deletes the `User` row and nothing else.
- **Open invitations** — email, expiry, *Cancel*.
- **Invite an admin** — email field. On submit the single-use link is shown
  once, in the same "copy this and send it however you already talk to them"
  pattern the client invite uses. GigLedger sends no email.

### First-run setup

When `User.query.count() == 0`, `/login` redirects to `/setup`, which asks for
business name, email and password, creates the `Business` row and the first
admin, and signs them in. `/setup` returns 404 as soon as any user exists, so
it cannot later be used to add an account.

### The demo seed no longer runs on its own

`create_app()` today seeds `demo@gigledger.com / demo1234` into any empty
database. Under one business that is a known-password admin who sees
everything, on every fresh install. The seed runs only when `SEED_DEMO=1` is
set; without it a fresh database starts at `/setup`. The seed itself is
otherwise unchanged (it still creates the demo user and data) and is corrected
to advance `Business.next_invoice_number` past the invoices it creates.

## Settings page

Three clearly labelled areas, in this order:

1. **Business** — name, address, phone, tax rate, currency, invoice prefix,
   invoice note, categories. A one-line note: *"These settings are shared by
   every admin."*
2. **Team** — as above.
3. **My account** — email, password, theme, dark mode.

## Client Portal

`portal/index.html` stops grouping documents by owner. The page heading is
`business.name`; below it, projects, then documents — the same rows, one level
shallower. `visible_clients()` and every grant and download check are
unchanged. The comment in `portal_auth.py` and the glossary entries that
describe the portal account as "the schema's one cross-tenant object" are
rewritten: there is no longer a tenant to cross, and the reason a portal
session is not a Flask-Login session is now solely that a client must never be
`current_user`.

## Migration

In `_migrate_db`, idempotent, in this order:

1. `CREATE TABLE IF NOT EXISTS business (...)` and `admin_invites (...)`.
2. `ALTER TABLE users ADD COLUMN last_login_at DATETIME` if missing.
3. If `business` is empty and `users` is not: insert one row copied from the
   business columns of the lowest-id user. On the live database that is
   `demo@gigledger.com` — name "Demo Freelance Studio", prefix `INV`, counter
   15 — so invoice numbering continues without a gap.
4. If both tables are empty, do nothing; `/setup` will create the row.

The old business columns on `users` are left in place and simply stop being
read.

## Rollout on ledger.danismithdesign.com

1. Back up `gigledger.db` (the run wrapper already does this).
2. Deploy. The first request runs the migration above.
3. Sign in as `dani@danismithdesign.com`, rename the business on the Business
   settings page, then remove `demo@gigledger.com` from the Team panel.
4. Update `TEST_CREDENTIALS.md`.

The Okafor and Raman clients, projects, invoices, documents, grants and portal
accounts are not touched by any step. Once renamed, portal clients see "Dani
Smith Design" as their heading.

## Deferred

- Roles or per-admin permissions. Every admin is equal.
- Showing "created by" anywhere in the UI.
- Email delivery of invitations.
- Dropping the dead business columns from `users`.
- Supporting more than one business on an install (declined in brainstorming).

## Testing

**Inverted tests.** `test_inventory.py`, `test_documents.py`,
`test_document_sharing.py` and `test_portal_auth.py` each assert that a second
user cannot see the first user's row. Those cases become *a second admin sees
the same row* — same fixtures, flipped expectation.

**Fixtures.** A new `tests/conftest.py` provides `app`, `business` and `admin`
fixtures, replacing the ten places that today construct a `User` with
business columns.

**New tests.**
- Migration: seeds `business` from the lowest-id user; a second run changes
  nothing; `next_invoice_number` continues from the seeded value.
- Invites: issue returns a token once; redeem creates the admin and signs them
  in; expired, redeemed and unknown tokens render the same page with the same
  status; an invite for an existing admin's email is refused; reissue closes
  the previous one.
- Team: cannot remove yourself; cannot remove the last admin; removing an
  admin leaves their rows in place.
- Setup: `/setup` creates business and first admin on an empty database and
  returns 404 afterwards; `/login` redirects to it only while there are no
  users.
- `/signup` returns 404.
- Portal index shows `business.name` once and no owner grouping.
- Seed does not run without `SEED_DEMO=1`.

The existing `test_no_handbuilt_html.py` and `test_template_escaping.py`
cover the new templates without changes.

## ADR

ADR-0014 "One business per install" records the decision that the install is
single-business, that `@login_required` is the access rule, that `user_id` is
attribution only, and that ADR-0008's scoping premise is superseded while its
principal separation stands.
