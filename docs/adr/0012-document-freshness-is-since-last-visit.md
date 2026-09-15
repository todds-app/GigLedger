# 0012. Flag a document as new by recency for the owner and by last visit for the client

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Project documents ([ADR-0006](0006-document-storage-and-serving.md)) lived
only on the project detail page. Nothing on the Projects list said a project
had any, and neither the owner nor a client got a cue that a file had arrived.
The ask was a count and a link on each project card, and an in-app "new"
marker for both readers.

The two readers are not in the same position, and one rule for both would be
wrong for one of them:

- **The owner added the file.** There is nothing to notify them of. What is
  useful on the list page is a recency cue — *something changed here lately* —
  and recency needs no stored state.
- **The client did not.** A document becomes visible to them at the moment a
  [share grant](0009-share-documents-by-explicit-grant.md) is created, and
  that moment is what "new" has to mean. The obvious refinement, "new until
  opened", is available for uploads — a portal download writes a
  `DocumentAccess` row — but **not for links**: opening a Drive link goes
  straight to Google and never touches GigLedger
  ([ADR-0007](0007-google-drive-as-reference.md)). A read-receipt rule would
  clear honestly for uploads and never for links, and the two kinds already
  differ in enough ways that a third, silent one is not welcome.

There is also a guard in the test suite (`test_csrf.py`) against GET routes
that write, and recording a visit is a write on a GET.

## Decision

**For the owner, a project card flags "New this week" when any document on
the project was created within `RECENTLY_ADDED` (seven days).** Computed from
`ProjectDocument.created_at`; no state is stored. The count and the flag come
from one grouped query per page (`documents.per_project_stats`), not a lazy
load per card.

**For the client, a document is "New" when a grant to one of the account's
clients was created after the account last loaded the portal home.** The
`PortalAccount` carries `documents_seen_at`; the home page computes the set
of new ids from `DocumentShare.created_at` *before* stamping the visit, then
stamps it. Freshness is per portal account, not per client row, because the
account is the person reading the page.

**The stamp is written from a GET, through `documents.mark_documents_seen`,
on the same footing as `record_access`.** Both record something the reader
did rather than perform something they asked for. A forged cross-site GET
can, at worst, clear a badge — the same order of harm as a forged download
adding an access row — and the forger cannot read the response either way.

## Consequences

- A refresh clears the client's badges. That is what "since your last visit"
  means everywhere else, and it is stated on the page in those words.
- Re-saving the share form with the same clients does not re-flag: the share
  route inserts only new grants and deletes withdrawn ones, so an unchanged
  grant keeps its timestamp. Revoking and re-granting *does* re-flag, which is
  correct — the client lost and regained access.
- The owner gets no read receipts. Whether a client has opened a file is
  answerable for uploads from `DocumentAccess` and not for links; surfacing
  it would reintroduce the asymmetry this decision avoids.
- Existing installs need the column: `_migrate_db` adds
  `portal_accounts.documents_seen_at`, and its absence on a fresh install is
  left to `create_all()`.
- A first visit (`documents_seen_at` null) flags everything shared so far,
  including on the page a client lands on after redeeming an invite. That is
  the intended reading: it is all new to them.
