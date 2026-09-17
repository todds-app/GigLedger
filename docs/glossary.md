# Glossary

Shared vocabulary for this codebase. Terms are defined here so that discussions,
ADRs, comments, and code all use the same word for the same thing.

---

## Naming

### Canonical Name

**GigLedger.** The single name the project is called, from which every other
name derives: the package directory, the database filename, the `package.json`
name, the systemd instance, and both git remotes.

Settled in [ADR-0001](adr/0001-declare-package-identity.md). The codebase
previously carried two names — `FreelanceCash` internally and `GigLedger`
externally — as the result of an unfinished rename inherited from upstream.

### Display Name

The name shown to a human: page titles, flash messages, the PDF report header,
the README prose. Purely cosmetic; changing it cannot break code. Distinguished
from **Package Identity** precisely because the two have different blast radii
and are therefore changed in separate commits.

### Package Identity

The importable Python name of the application package — the string `X` in
`import X.app`. Here it is `gigledger`, fixed by the name of the
[gigledger/](../gigledger/) directory inside the repository.

The distinction that matters: package identity is **declared**, not inferred
from where the repository happens to be checked out. Deriving it from the
filesystem — by hardcoding a folder name, by reading `basename(__file__)`, or by
symlinking — makes it a fact nothing can verify. See
[ADR-0001](adr/0001-declare-package-identity.md).

---

## Structure

### Entry Point

[run.py](../run.py) — the file executed to start the server, living at the
repository root, *outside* the application package. It makes the app and hands
it to a WSGI server. It needs no `sys.path` manipulation, because Python places
a script's own directory at `sys.path[0]`, which is exactly the directory that
contains the `gigledger/` package.

### Application Factory

`create_app()` in [gigledger/app.py](../gigledger/app.py) — builds and
configures the Flask `app` object. Standard Flask pattern: configuration,
extension binding, and blueprint registration happen here rather than at import
time.

### Data Path

`DB_PATH` in [gigledger/app.py](../gigledger/app.py) — the absolute path to the
SQLite database, derived once at module level and consumed by both
`create_app()` and `_migrate_db()`.

It resolves to the **repository root**, one level above the package, so that
user data does not live inside the importable source tree. It is anchored to the
code rather than the working directory, so the app finds its database wherever
it is launched from. See [ADR-0002](adr/0002-database-path.md).

---

## Security

### Unsafe Method

Any HTTP method that may change state — `POST`, `PUT`, `PATCH`, `DELETE`.
CSRF protection covers exactly these, which is why a state-changing `GET` (as
`/logout` was) is invisible to it. `tests/test_csrf.py` asserts no safe-method
route mutates state.

### CSRF Field

`{{ csrf_field() }}` — a Jinja global registered in `create_app()` that renders
the hidden token input. Called once inside every POST form. Exists so the markup
has one source of truth rather than being duplicated 48 times. See
[ADR-0003](adr/0003-csrf-protection.md).

### Vacuity Check

A test whose only job is to prove another test can fail. `test_the_probe_is_not_vacuous`
runs the CSRF enforcement probe with protection *disabled* and asserts routes do
act; if they don't, the probe has stopped reaching the handlers and the
enforcement test proves nothing. Written because the first version of the
enforcement test passed for the wrong reason — `@login_required` was refusing
the requests before CSRF was ever consulted.

### Event-Handler Context

An `on*="..."` attribute — JavaScript living inside an HTML attribute, so it
passes through **two** parsers. The HTML parser decodes entities *before* the
JavaScript engine sees the value, which is why HTML-escaping alone (`| e`) does
not make it safe: the escaped quote is decoded back into a real quote and closes
the string literal early.

The rule for this context is `| tojson | forceescape`, enforced by
`tests/test_template_escaping.py`. Distinct from a `<script>` body, where bare
`| tojson` is correct. See [ADR-0004](adr/0004-template-escaping-in-event-handlers.md).

### Export Document

A self-contained HTML file the user downloads and opens from disk —
`templates/invoices/export.html` and `templates/transactions/export.html`.
Deliberately does not extend `base.html`: it carries its own styles and no
navigation, because it is read outside the app.

Rendered from a template rather than built in the route so that autoescape
applies by default. `tests/test_no_handbuilt_html.py` keeps routes from drifting
back to f-string HTML. See [ADR-0005](adr/0005-render-documents-from-templates.md).

### Constrained Column

A column that only ever holds a value from a fixed set — `Project.rate_type`,
`Project.color`, `Goal.icon`, `Goal.color`, `Project.status`,
`ProjectDocument.kind`, `ProjectDocument.provider`. Validated at the write by a
`clean_*` helper rather than trusted from the form, so the set is enforced
rather than merely intended.

`ProjectDocument.external_url` is the same idea applied to a *part* of a value:
the whole URL is free text, but its **scheme** is constrained to `http`/`https`
by `clean_external_url()`. HTML-escaping a URL protects the attribute's syntax
and says nothing about its meaning, so `javascript:` survives autoescape intact.

### Document Kind

`ProjectDocument.kind` — `upload` or `link`. The column that says which half of
the row is meaningful: `stored_name`/`original_name`/`byte_size` for an upload,
`external_url`/`provider` for a link.

One table rather than two, because the two kinds differ only in how content is
fetched and are identical for listing, deleting, and authorising. The reason
that matters: two tables means writing the authorisation check twice, and the
second copy is where it gets forgotten. See
[ADR-0006](adr/0006-document-storage-and-serving.md).

### Opaque Name

`ProjectDocument.stored_name` — the generated `uuid4` token a file is written
under. Distinguished from `original_name`, the name the user chose, which is
display data and **never** reaches the filesystem.

The distinction is the whole defence: a user-supplied name that reaches
`os.path.join` is a path, and `../` is a traversal. Only the extension crosses
over, and only one the allowlist already accepted.

### Upload Root

`UPLOAD_ROOT` in [gigledger/documents.py](../gigledger/documents.py) — the
absolute path to the uploads directory, derived once at module level exactly as
**Data Path** is, resolving to the repository root beside the database. Same
reasoning, same reason it is shared rather than recomputed: a test points it at
a temporary directory and every writer follows.

### Share Grant

A `DocumentShare` row — one document granted to one **Client**, unique on the
pair. A document with no grants is visible to every admin and to no client:
**private by default** on the portal side, so a mis-click leaks nothing
because there is nothing to mis-click into.

Points at a `Client` rather than a [[Portal Account]] deliberately: an admin
grants access to a client, and whether that client has ever signed in is a
separate question. Revoking portal access leaves the grants standing. See
[ADR-0009](adr/0009-share-documents-by-explicit-grant.md).

### Access Log

`DocumentAccess` — one row per document actually fetched, naming which kind of
principal read it. Written **only after authorisation succeeds**: a refused
request is not an access and must not read like one, which also means the table
does not show you someone probing for documents they were never granted.

### Enforced vs Advisory

The distinction that runs through every discussion of document access here.

For an **upload**, GigLedger holds the bytes and serves them only to the owner
and to holders of a [[Share Grant]] — the rule is *enforced*. For a **link**,
GigLedger controls who is shown the URL and Google controls who can open it —
the rule is *advisory*. The share form says so on link documents rather than
letting an identical-looking checkbox imply a guarantee it cannot make. See
**Reference, not Integration** below.

### Reference, not Integration

What "shared via Google Drive" means in GigLedger: the app stores a **URL**, and
Google decides who may open it. GigLedger holds no Google credentials and never
fetches the file.

The consequence to keep in mind whenever document access is discussed: the
access rule is **enforced** for uploads and **advisory** for links. The two must
never be presented to a user as equivalent. See
[ADR-0007](adr/0007-google-drive-as-reference.md).

### Business

`Business` — the one row holding business-wide settings. `Business.get()`
fetches it; templates receive it as `business`. See
[ADR-0014](adr/0014-one-business-per-install.md).

### Admin

An admin login (`User`). Every admin sees every row; `current_user` always
means an admin. Added by invitation from Settings → Team; see
[ADR-0014](adr/0014-one-business-per-install.md).

### Portal Account

`PortalAccount` — a client's login for the Client Portal. Keyed by email and
**global**, linked many-to-one from `Client` rows, because the same person is
routinely a client of several freelancers and one row per (freelancer, email)
makes the login form ambiguous.

It holds credentials and nothing else; `portal_auth.visible_clients()` is the
single seam between a login and the Client rows it may act through.

### Portal Session

The `portal_account_id` key in the Flask session — how a client is
authenticated. Deliberately **not** Flask-Login.

The distinction is not stylistic. `current_user` is an admin everywhere in
this app, and an admin sees every row (ADR-0014); if a client could become
`current_user`, a portal account would be an admin. A **Portal Session** and
an admin session are mutually exclusive, so no request has two principals.
See [ADR-0008](adr/0008-client-portal-authentication.md) and
[ADR-0014](adr/0014-one-business-per-install.md).

### Session Epoch

`PortalAccount.session_epoch` — an integer stamped into the session at login and
compared on every request. Revoking access or reissuing an invite bumps it,
which ends live sessions on their next request.

Exists because unlinking a client alone would leave an already-issued cookie
working until it lapsed. The epoch is what makes "revoke" mean *signed out now*.

### Invite Token

The one-time secret in a portal invitation URL. Generated with
`secrets.token_urlsafe(32)`, stored only as a SHA-256 hash, single-use, expiring
after seven days.

SHA-256 rather than bcrypt on purpose: it is a 256-bit random token, not a
password, so there is no dictionary to slow down — the only property needed is
that the stored form cannot be used as the token. Delivered by the freelancer,
not emailed, because **this app cannot send email** and an invite flow that
assumes SMTP is one that fails silently.

### Admin Invite

`AdminInvite` — a single-use, 7-day, hashed token that lets someone become an
admin; issued from Settings → Team; same rules as a portal invite. See
[ADR-0014](adr/0014-one-business-per-install.md).

### Secret Key

`SECRET_KEY`, read from the environment in `create_app()`. Signs session cookies
**and** derives CSRF tokens, so an ephemeral key logs everyone out *and*
invalidates every open form on restart.

Lives in `.env` — gitignored, sourced by `proj@.service`. Deliberately **not**
[autostart.env](../autostart.env), which is committed.

---

## Finance

### Transaction Kind

`Transaction.kind` and `RecurringTransaction.kind` — `income`, `expense` or
`inventory`. Stored, never derived from the sign of `amount`: a sign carries
one bit and the vocabulary has three members. The sign says which way the money
moved; the kind says what the movement was. [ADR-0010](adr/0010-classify-transactions-by-kind.md).

### Cost Kind

A kind that reduces profit. `COST_KINDS = {EXPENSE}` in `models.py` is the one
place that answers the question; inventory's absence from the set is the entire
behaviour that keeps it out of every expense total.

### Kind-Blind Figure

A figure that sums every kind on purpose because it measures cash, not cost:
Safe to Spend, Runway, and Monthly Commitments. Each looks like a missed
conversion to kind and is not one. Listed in ADR-0010 so nobody "fixes" them.

### Inventory Item

`InventoryItem` — the asset half of an inventory purchase, 1:1 with its
`Transaction`: quantity, unit cost, consumable or reusable, and where it was
bought for. The transaction's `amount` is always `-(quantity × unit_cost)`.
[ADR-0011](adr/0011-inventory-purchases-as-ledger-lines.md).

### General Inventory

Stock held for no particular job. Represented as `InventoryItem.project_id IS
NULL`, not as a sentinel project. Items whose project is deleted return here.

### Consumable / Reusable

`InventoryItem.is_consumable`. A consumable becomes a cost when used on a
project; a reusable is placed on a project and comes back, staying an asset
throughout. Both are piece 3 behaviour; piece 2 only records the flag.

### Time Log

`TimeLog` — a block of hours worked on a project: the hours, the date range
they cover, who logged them, and optionally the income `Transaction` they were
billed as (many logs may share one transaction: Bill all unbilled).
`Project.hours_logged` is the sum of its logs. A billed log is locked;
deleting its transaction unlocks it.
[ADR-0015](adr/0015-time-logs-with-a-server-side-timer.md).

### Invoiced (transaction)

An income or inventory `Transaction` that an `InvoiceLineItem` points at
(`transaction_id`, 1:1, unique). Shown in the Invoiced column on the
Transactions page. Locked - no edit, no delete - until its row is removed
from the draft (on the invoice page or its edit form) or the invoice is
deleted; the line itself may be edited on the draft. Marking an invoice Paid posts only
the Tax Reserve expense; the income is the line already in the ledger.
[ADR-0016](adr/0016-bill-transactions-on-invoices.md).

### Timer

The per-project clock on the Projects page. Its state is three columns on
`Project` - `timer_started_at`, `timer_seconds` (banked, unlogged) and
`timer_since` (first start of the unlogged stretch) - so it survives a reload
or restart. One runs at a time; Log Hours turns it into a Time Log and resets
it. Seconds become hours by `round_quarter`: nearest quarter hour, half up.

---

## Deployment

### Project Manifest

[autostart.env](../autostart.env) — declares `AUTOSTART_DESC`, `AUTOSTART_PORT`,
and `AUTOSTART_EXEC`. Read by `proj-run`, which the `proj@GigLedger.service`
systemd user unit invokes. Committed, by convention with the sibling projects.

### Instance Name

The `%i` in `proj@GigLedger.service` — resolved by `proj-run` to a single
directory under `~/projects`. **This is still coupled to the checkout directory
name**, deliberately and separately from package identity. Renaming the checkout
requires updating the systemd instance and the absolute path in the manifest;
it no longer affects Python imports.

---

## Provenance

### Upstream

`Cyber-Dioxide/GigLedger` — the repository this project was forked from. Treated
as a hard fork: no further merges are expected, which is what makes structural
divergence such as [ADR-0001](adr/0001-declare-package-identity.md) acceptable.

### Origin

`todds-app/GigLedger` — this fork.
