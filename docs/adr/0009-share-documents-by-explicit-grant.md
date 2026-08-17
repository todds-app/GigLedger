# 0009. Share documents by explicit grant to a client; private by default

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

[ADR-0006](0006-document-storage-and-serving.md) settled how documents are
stored and served, and said plainly that it did not address *who else* may see
one: in that commit, "may see it" meant "owns it".
[ADR-0008](0008-client-portal-authentication.md) then gave clients a way to sign
in. This decision is the join between them, and it is where the requirement —
*only the selected clients and site admins can view the documents* — finally
becomes something the code enforces rather than something the UI implies.

Three things had to be decided, and each had a cheaper wrong answer.

**What a document is shared with.** The obvious model is a boolean —
`is_client_visible` — resolving to the project's client, since `Project.client_id`
holds exactly one. It is one column instead of a table. It also cannot express
"this contract goes to the client, that internal estimate does not, and this
spec also goes to the subcontractor", which is the ordinary case rather than an
exotic one.

**What "shared with" points at.** A grant could name a `PortalAccount` (the
thing that logs in) or a `Client` (the thing the freelancer knows about). These
diverge whenever access is revoked and later restored.

**What the portal reveals in order to organise the list.** Documents have to be
grouped to be usable, and grouping means showing something about the project.
A `Project` carries `description`, `rate`, `rate_type` and `hours_logged` —
internal notes and pricing. A client granted one file must not learn a day rate
from the page that lists it.

## Decision

**A `document_shares` table, one row per (document, client), unique on the
pair.** A document with no rows is visible to its owner alone. Default private,
so a mis-click leaks nothing because there is nothing to mis-click into.

**A grant names a `Client`, not a `PortalAccount`.** The freelancer grants
access to a client of theirs; whether that client has ever signed in is a
separate question. Revoking portal access unlinks the account and leaves the
grants standing, ready if access is granted again — which is what a freelancer
means by "revoke their login", not "forget everything I ever shared".

**The share form posts the complete set of clients.** A client absent from the
post is a client whose access is withdrawn. Unchecking a box revokes, and there
is no separate unshare route that could be forgotten or guarded differently.

**Requested client ids are intersected with the owner's clients, not
validated.** An id belonging to somebody else is dropped rather than rejected,
so a crafted form cannot grant across tenants and does not learn whether the id
it guessed exists.

**The portal shows the project's name and nothing else about the project** — no
description, no rate, no hours, no dates. The name itself is still exposure:
project names routinely contain a *different* client's name ("Acme Rebrand").
That is disclosed where the decision is made, in the share form: *anyone you
share this with can also see the project's name, "…"*.

**The portal groups by the freelancer's business name, then by project.**
`PortalAccount` is global, so a page can carry material from two freelancers who
do not know about each other. Rendering it as one undifferentiated list would
leak context even though every individual row is authorised.

**Every successful fetch writes a `document_accesses` row**, recording the
document, which kind of principal read it, the IP, and when. Written only after
authorisation succeeds: a refused request is not an access and must not read
like one.

**The portal's download is its own route**, not a branch inside the owner's.
"Do you own it" and "was it granted to you" are different questions, and keeping
them in separate handlers means neither can be reached with the other's answer.
Both return a bare 404 for every failure — not shared, does not exist, is a
link — because which one it is would itself be information.

## Consequences

- The access rule is now **enforced** for uploaded documents: the bytes are
  served only to the owner and to clients holding a grant. For **link**
  documents it remains **advisory**, exactly as
  [ADR-0007](0007-google-drive-as-reference.md) warned — GigLedger controls who
  is shown the link, and Google controls who can open it. The share form says
  so on link documents specifically, rather than letting the checkbox imply a
  guarantee it cannot make.
- Sharing is allowed to any of the owner's clients, including one with no
  portal access at all. The share UI marks those "no portal access" rather than
  hiding them, so a grant made ahead of an invitation is a deliberate act rather
  than a silent no-op.
- `document_accesses` grows without bound and nothing prunes it. At this scale
  that is fine; it is noted so that its absence is a decision rather than an
  oversight.
- The access log records reads, not attempts. It will not show you someone
  probing for documents they were never granted — that is a different table and
  a different question, and the 404s are indistinguishable by design.
- Deleting a document or its project removes the grants and the access rows with
  it, via the same ORM cascade ADR-0006 describes — and with the same caveat,
  that the cascade lives in the code rather than in the schema.
- **This is a fourth ADR where three were planned.** Sharing was originally
  scoped inside ADR-0006, but 0006 was already Accepted and describes storage
  and serving; retrofitting the access model into it would have made a settled
  document tell two stories. The decisions here have their own context and
  deserve their own file.
