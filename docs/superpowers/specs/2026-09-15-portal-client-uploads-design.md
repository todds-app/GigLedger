# Client uploads from the portal

- **Date:** 2026-09-15
- **Status:** Implemented (ADR-0013)
- **Scope:** One piece. A portal client can add a file or a Drive link to a
  project they are the client of; the freelancer sees who added it.
- **Builds on:** ADR-0006 (storage), ADR-0007 (links as references), ADR-0008
  (portal sessions), ADR-0009 (explicit grants), ADR-0012 (freshness).

## Problem

Documents flow one way. A freelancer uploads or links a file and grants it to a
client; the client can only read. The files that matter on a project — floor
plans, design boards, spreadsheets — often start on the client's side, and
today the client has to email them, after which the freelancer re-uploads them.
The portal should accept them directly, with the same storage rules as the
freelancer's own uploads and a clear record of who put them there.

## Decisions

### The portal lists projects, not only shared documents

The portal home currently shows documents that have been granted, grouped by
freelancer then project. A client cannot add to a project they cannot see, and
a project with nothing shared yet is invisible. So the home page lists **every
project whose `client_id` is one of the account's Client rows**, grouped by
freelancer as now, each with the documents the account may see and two forms:
Upload and Add link.

ADR-0008's rule stands: only the project's *name* is shown. A project with no
visible documents reads "No documents yet".

A project whose `client_id` is null belongs to nobody in the portal and is
never listed.

### A client-added document is the freelancer's document, attributed

```python
class ProjectDocument:
    added_by_client_id = FK clients.id, nullable   # NULL = added by the owner
```

The row is created with `user_id = project.user_id` — it is the freelancer's
document in every existing query, listing, share and delete path — and the
same commit creates a `DocumentShare` to `project.client_id`, so the client who
added it sees it at once. The freelancer's project page shows "Added by
<client name>" on the row.

Attribution names the **Client** row, not the PortalAccount, for the reason
ADR-0009 grants to a Client: it is the identity the freelancer recognises, it
is tenant-scoped, and it survives portal access being revoked and reissued.
The `DocumentAccess` log continues to name the account for downloads; that is
an audit of a login, this is provenance of a file.

**Only the owner removes or re-shares.** The portal gets no delete route and no
share route. A client who uploaded the wrong file asks; the freelancer may
already be relying on the right one.

### Two routes, one authorisation question

```
POST /portal/projects/<int:project_id>/documents/upload
POST /portal/projects/<int:project_id>/documents/link
```

Both `@client_required`. The check, in one helper: the project exists and its
`client_id` is among `visible_clients(account)`. Anything else is a 404, as
the portal download route already answers — which of "no such project", "not
yours" and "no client on it" would itself be information.

Storage, the extension allowlist and rejection list, the 25 MB cap, opaque
names, and `clean_external_url` are reused unchanged from `documents.py`. The
portal routes are thin: authorise, call the same functions the owner's routes
call, add the share, commit, flash, redirect to the portal home.

### Alerts in both directions

- **Owner.** ADR-0012's card pill says "New this week" for a recent document.
  When a recent document was added by a client, the pill says **"New from
  client"** instead — that is the case where the owner genuinely did not know.
  `per_project_stats` grows a third value: the most recent `created_at` among
  client-added documents.
- **Client.** A document the account's own clients added is excluded from
  `newly_shared_ids`: it was not shared *with* them. Without this, every upload
  would greet its uploader with "1 document shared with you since your last
  visit".

### What is deliberately not done

- No per-client quota. ADR-0006 said the operator owns the disk; the operator
  also chooses whom to invite. Stated as an accepted risk in the ADR.
- No approval step. The document is live to the freelancer and back to the
  client immediately.
- No portal-side rename, replace or delete.
- No notification by email.

## Migration

`_migrate_db`: `PRAGMA table_info(project_documents)`; if the table exists and
lacks `added_by_client_id`, `ALTER TABLE project_documents ADD COLUMN
added_by_client_id INTEGER REFERENCES clients(id)`. Existing rows are
owner-added, which null already means.

## User interface

**Portal home.** Each project group gains an "Upload" and an "Add link" button
opening the same two modals the owner's project page uses (copied into the
portal template, not shared — the portal has its own base and its own copy
about what a link is). The Drive-link caveat text appears on the link form.
Flash messages already render in the portal base.

**Owner's project detail.** Rows added by a client show "Added by Acme Corp"
in the grey metadata line. The Documents subtitle changes from "Visible only
to you until you share one with a client" to "Yours and your client's, in one
place. Share a document to make it visible in the portal." — because a
client-added row is *already* visible to that client.

**Owner's project card.** "New from client" pill, styled like "New this week"
but amber, so it reads as an inbound item rather than a recency note.

## Testing

All in `tests/test_document_sharing.py` unless noted; written before the code.

- A client can upload to a project they are the client of; the row's
  `user_id` is the freelancer's, `added_by_client_id` is theirs, and a share
  to them exists.
- A client can add a link the same way; provider is detected.
- Uploading to a project of a client the account does not hold is a 404.
- Uploading to a project with no client is a 404.
- A rejected extension and a non-http scheme are refused through the portal
  with the same outcome as through the owner's routes (no row, no bytes).
- There is no portal delete route: `POST /portal/documents/<id>/delete` is
  404/405, and the owner's delete route refuses a portal session.
- The portal home lists a project with no documents once the account holds
  its client, and does not list a project of another client.
- The owner's project page shows "Added by <client name>".
- The uploader's own document is not flagged new to them on the next load.
- `tests/test_documents.py`: the owner's card says "New from client" for a
  recent client-added document, and "New this week" for a recent owner-added
  one.
- Migration adds the column to an existing `project_documents` table.
- `test_csrf.py`'s probes pick up the new POST routes automatically; the
  safe-method guard must stay green.

## ADR

ADR-0013: *Accept documents from portal clients as the owner's, attributed to
the client.* Records the ownership choice, the attribution-by-Client choice,
the no-delete rule, and the accepted disk-write risk.
