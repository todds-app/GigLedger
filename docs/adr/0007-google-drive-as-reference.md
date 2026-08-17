# 0007. Reference Google Drive documents by link; do not integrate with Drive

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

Project documents were specified as attachable in two ways: uploaded to
GigLedger, or "shared via Google Drive." Those two phrases sound parallel and
are not.

Three implementations were considered:

1. **Reference.** The user pastes a Drive URL; GigLedger stores the URL, a
   title, and which provider it points at.
2. **Integration.** OAuth against Google, a file picker, metadata sync, and
   GigLedger proxying the download so it can apply its own access rules.
3. **Import.** OAuth, then copy the bytes into GigLedger once, after which the
   document is an ordinary upload.

The requirement that forced the decision to be explicit was the access rule:
*only the selected clients and site admins can view the documents.* Option 1
**cannot deliver that** for a Drive file, and it is worth being blunt about why.
GigLedger would store a URL. Whether a given person can open that URL is decided
by Google, from Drive's own sharing settings. If the file is shared "anyone with
the link," then GigLedger's access control governs who is shown the link, not
who can open the file — and a link, once shown, can be forwarded. If the file is
restricted in Drive to people GigLedger has never heard of, the client clicks
through to a Google permission wall that neither they nor GigLedger can resolve.

Option 2 is the only one that makes the stated rule true for Drive files, and it
is a genuinely large piece of work: an OAuth consent screen and its review,
refresh tokens encrypted at rest, token expiry handled in an app with no
background jobs, and a proxy download path that becomes a new SSRF surface —
GigLedger fetching a URL on a user's behalf is exactly the shape
[ADR-0005](0005-render-documents-from-templates.md) removed from this codebase.

## Decision

**We store a reference, not an integration**, and we say so in the interface
rather than letting the user infer it.

A link document keeps a URL, a title, and a `provider` — `google_drive` when the
host is a recognised Drive host, `other` otherwise. GigLedger never authenticates
to Google, never holds a Google token, and never fetches the linked file. The
scheme is constrained to `http`/`https` at the write, because the URL is
interpolated into an `href` where autoescape does not defuse `javascript:`.

The document list labels Drive links with **"access is managed where the file
lives, not in GigLedger,"** and the add-link form says the same thing before the
user commits. The limitation is disclosed at both the moment of creation and the
moment of reading.

## Consequences

- The feature costs no new dependencies, no new secrets at rest, and no new
  outbound network calls. A self-hosted GigLedger continues to talk to nothing.
- **The access rule is honest for uploads and advisory for links.** An uploaded
  document is served only to principals GigLedger has authorised. A linked
  document is only as private as Drive makes it. Any future document-sharing UI
  must not present the two as equivalent, because they are not.
- Nothing is checked about the far side of the link. A stale link, a moved file,
  or a revoked share surfaces as a Google error page, not as a GigLedger one.
  This is the honest failure: GigLedger genuinely does not know.
- The `provider` column exists to make Drive links identifiable in the UI, and
  it is the seam an integration would grow from. If option 2 is ever wanted,
  this decision should be **superseded rather than patched**, and the work it
  implies — consent screen, token storage, refresh, an SSRF-safe fetcher — is
  named in the Context above so nobody re-discovers the cost.
- Import (option 3) is deliberately left unbuilt but is the cheap middle path if
  the access rule turns out to matter more than staying in sync: it needs OAuth
  but no proxy and no ongoing token use.
