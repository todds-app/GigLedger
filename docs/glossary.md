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
`Project.color`, `Goal.icon`, `Goal.color`, `Project.status`. Validated at the
write by a `clean_*` helper rather than trusted from the form, so the set is
enforced rather than merely intended.

### Secret Key

`SECRET_KEY`, read from the environment in `create_app()`. Signs session cookies
**and** derives CSRF tokens, so an ephemeral key logs everyone out *and*
invalidates every open form on restart.

Lives in `.env` — gitignored, sourced by `proj@.service`. Deliberately **not**
[autostart.env](../autostart.env), which is committed.

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
