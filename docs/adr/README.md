# Architecture Decision Records

One file per decision, named `NNNN-short-slug.md`, numbered in the order the
decisions were made. An ADR records *why* a choice was made, so that a future
reader can tell a deliberate decision from an accident.

An ADR is written when a decision is **settled**, not while it is being debated.

## Format

```markdown
# NNNN. Title stated as the decision

- **Status:** Proposed | Accepted | Superseded by [NNNN](NNNN-slug.md)
- **Date:** YYYY-MM-DD

## Context

The situation that forced a decision. Facts, constraints, and what actually went
wrong — including anything that turned out to be a misdiagnosis.

## Decision

What was chosen, in the active voice: "We derive X from Y."

## Consequences

What this makes easy, what it makes hard, and what now has to stay true. Include
the failure modes the decision does *not* protect against.
```

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-declare-package-identity.md) | Declare package identity instead of deriving it from the filesystem | Accepted |
| [0002](0002-database-path.md) | Keep the database in the repository root, derived once | Accepted |
| [0003](0003-csrf-protection.md) | Enforce CSRF protection app-wide with Flask-WTF | Accepted |
| [0004](0004-template-escaping-in-event-handlers.md) | Escape event-handler interpolations with `tojson` + `forceescape` | Accepted |
| [0005](0005-render-documents-from-templates.md) | Render export documents from templates; drop the WeasyPrint branch | Accepted |
| [0006](0006-document-storage-and-serving.md) | Store project documents under opaque names; serve them only as attachments | Accepted |
| [0007](0007-google-drive-as-reference.md) | Reference Google Drive documents by link; do not integrate with Drive | Accepted |
| [0008](0008-client-portal-authentication.md) | Authenticate portal clients outside Flask-Login, in their own session | Accepted |
| [0009](0009-share-documents-by-explicit-grant.md) | Share documents by explicit grant to a client; private by default | Accepted |
| [0010](0010-classify-transactions-by-kind.md) | Classify transactions by an explicit kind, not the sign of the amount | Accepted |
| [0011](0011-inventory-purchases-as-ledger-lines.md) | Inventory purchases are ledger lines with a linked asset row | Accepted |
| [0012](0012-document-freshness-is-since-last-visit.md) | Flag a document as new by recency for the owner and by last visit for the client | Accepted |
| [0013](0013-accept-documents-from-portal-clients.md) | Accept documents from portal clients as the owner's, attributed to the client | Accepted |
| [0014](0014-one-business-per-install.md) | One business per install | Accepted |
| [0015](0015-time-logs-with-a-server-side-timer.md) | Hours are time logs, counted by a timer on the project row | Accepted |
| [0016](0016-bill-transactions-on-invoices.md) | The ledger is the income record; an invoice bills lines already in it | Accepted |
