# 0007. Store project documents on disk under opaque names; serve them only as attachments

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

Projects needed documents attached to them — contracts, briefs, spreadsheets —
some uploaded, some living in Google Drive. Before this change the app stored no
files at all: there was no upload handling, no `static/` directory on disk, and
nothing anywhere that wrote a byte outside SQLite.

That absence is what made the decision worth recording. Adding file storage to a
web app introduces three failure modes that have nothing to do with files and
everything to do with what a browser does when you hand it one:

1. **A user-chosen filename that reaches the filesystem is a path.** `../` in a
   name is the entire class.
2. **A user-uploaded file served inline from the app's own origin is stored
   XSS.** An `.html` or `.svg` attachment, rendered as a page, runs script
   against a live session cookie. This is a strictly worse version of the bug
   [ADR-0005](0005-render-documents-from-templates.md) was written about: there
   the injected markup rendered from a `file://` origin with no cookie to steal.
3. **A link is not a file.** A Drive URL is interpolated into an `href`, and
   Jinja's autoescape does nothing about a `javascript:` scheme — escaping
   protects the attribute's syntax, not its meaning.

A fourth constraint came from the schema rather than the browser: **SQLite does
not enforce foreign keys** unless `PRAGMA foreign_keys=ON` is set per
connection, which this app has never done. `ON DELETE` clauses here are
decorative.

## Decision

**One table, `project_documents`, with a `kind` discriminator** in
`{'upload', 'link'}` and nullable per-kind columns. An upload and a link differ
only in how content is fetched; listing, deleting, and authorising are
identical. Two tables would mean writing the authorisation check twice, and the
second copy is where it gets forgotten.

**Uploaded bytes live in `uploads/` at the repository root** — beside the
database, outside the importable package, for the reason already settled in
[ADR-0002](0002-database-path.md): user data does not belong in the source tree.
`UPLOAD_ROOT` is derived once in `gigledger/documents.py` and shared, so a test
can point it somewhere disposable and every writer follows.

**The name on disk is generated, never chosen.** A `uuid4` hex token carries
over only the extension, and only an extension the allowlist already accepted.
The name the user recognises is a column, used for display and for the
`download_name` of the response. No user-supplied string reaches `os.path.join`.

**Downloads are always `attachment` with `application/octet-stream`.** The
uploaded file's actual type is never consulted. Combined with an extension
allowlist that is backed by an **explicit rejection list** — `.html`, `.htm`,
`.xhtml`, `.shtml`, `.svg`, `.xml` — this is defence in depth rather than one
control: the allowlist protects by omission, and omission is exactly what erodes
when somebody adds a type later, so the dangerous ones are named.

**A link document's URL is constrained to `http`/`https` at the write**, the
same shape as the existing `clean_*` helpers for the Constrained Columns.

**Cascade is implemented in the ORM, and `PRAGMA foreign_keys` stays off.**
Enabling it retroactively on a database that has accumulated rows without
enforcement can turn an ordinary delete into an `IntegrityError` on data that
predates the constraint — a migration risk taken for no benefit, since
SQLAlchemy's `delete-orphan` works either way. Deleting a project cascades to
its document rows; the route then unlinks the files, **after** the commit
succeeds, because no ORM cascade touches a filesystem.

**No per-user quota.** A 25 MB per-file cap via `MAX_CONTENT_LENGTH`, enforced
by Flask before the request body is read. This is self-hosted; the operator owns
the disk, and a quota would be a support burden with no beneficiary.

## Consequences

- **The cascade is a property of the code, not of the schema.** It holds only
  for deletes that go through the ORM. A `DELETE FROM projects` in a SQLite
  shell leaves orphaned rows and orphaned bytes. This is the cost of not
  enabling the pragma, and `test_deleting_a_project_deletes_its_documents_and_their_bytes`
  is what keeps the code half honest.
- Uploads are inspectable by a human — the extension survives — but not
  attributable: matching a file on disk to a project requires the database.
  That is the intended trade.
- `uploads/` is gitignored in the same commit that creates it. An upload
  directory that appears in `git status` before anyone thinks about it is how
  client contracts end up in version control.
- The 25 MB cap is enforced by Flask, which returns a bare `413`. There is no
  friendly flash message, because by the time a handler could flash one the body
  has already been read — which is the thing the cap exists to prevent.
- Replacing a document is not implemented: remove and re-upload. There is no
  version history, deliberately — history means retention policy, disk growth,
  and a UI, none of which "the client should see the current contract" requires.
  Note the asymmetry this creates: a **Drive-linked** document versions itself,
  invisibly, in Drive. The two kinds behave differently here and users should be
  told so rather than discover it.
- Nothing in this decision addresses *who else* may see a document. In this
  commit "may see it" means "owns it". The client-facing half arrives with the
  Client Portal ([ADR-0008](0008-client-portal-authentication.md)), and the
  ownership filter written here is what it extends.
