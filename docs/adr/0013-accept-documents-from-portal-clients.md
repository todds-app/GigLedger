# 0013. Accept documents from portal clients as the owner's, attributed to the client

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

Until now documents flowed one way: the freelancer uploaded or linked a file
and granted it to a client ([ADR-0009](0009-share-documents-by-explicit-grant.md));
the client could only read. The files that matter on a project — floor plans,
design boards, spreadsheets — often start on the client's side, and the
round trip was email, then a re-upload by the freelancer.

Letting the portal accept files opens a write path from a semi-trusted
principal into the freelancer's data, and three questions had to be answered
before it could:

1. **Whose document is it?** A client-owned row would need its own listing,
   sharing and deletion paths, and every query that today says
   `user_id == current_user.id` would need a second branch.
2. **Who is recorded as having added it?** The `PortalAccount` is the login;
   the `Client` is the tenant-scoped identity the freelancer knows.
3. **Can the client take it back?** A document the freelancer has acted on —
   quoted from, forwarded, invoiced against — disappearing on a client's
   second thought is a worse failure than a client having to ask.

## Decision

**A client-added document is the owner's document.** The row is created with
`user_id = project.user_id`. Every existing path — the owner's list, share
form, delete route, cascade on project deletion — applies to it unchanged,
because nothing distinguishes it there.

**It is attributed to the `Client`, in `ProjectDocument.added_by_client_id`.**
Null means the owner added it. The Client row rather than the PortalAccount,
for the reason ADR-0009 grants to a Client: it is the identity the owner
recognises, it is tenant-scoped, and it survives portal access being revoked
and reissued. The `DocumentAccess` log continues to name the account for
downloads — that is an audit of a login; this is provenance of a file.

**It is granted back to that client in the same commit.** The person who
added it sees it at once, through the ordinary share mechanism, and the
grant is what makes it visible — not the attribution.

**Only the owner removes or re-shares.** The portal has no delete route and
no share route.

**The portal lists every project the account is the client of**, documents or
not, because a client cannot add to a project they cannot see. A project
reached only through a share grant is listed with its shared documents and
nothing can be added to it: the grant gave access to one document, not to
the project. ADR-0008's rule stands — only the project's name is shown.

**The client's own addition is never "new" to them.** ADR-0012's "shared with
you since your last visit" excludes documents added by the account's own
clients. The owner's card, conversely, says **"New from client"** for a
recent client-added document — the case where the owner genuinely did not
know.

**Authorisation is one question**, in one helper: does the project exist and
is its `client_id` among the account's visible clients. Anything else is a
404, as the download route already answers.

## Consequences

- **An invited client can write to the owner's disk.** Up to 50 MB per file,
  no quota — ADR-0006's "the operator owns the disk" now includes the people
  the operator has invited. Revoking portal access closes the path; it does
  not remove what was written.
- Storage rules are inherited, not re-implemented: the extension allowlist
  and rejection list, opaque names, attachment-only downloads and the
  `http`/`https` constraint on links all apply, and are tested through the
  portal routes as well as the owner's. A client-pasted URL is interpolated
  into an `href` on the *owner's* page; the scheme constraint is what makes
  that safe.
- A project with no client (`client_id` null) is unreachable from the portal.
- A client who uploaded the wrong file asks. That is the intended friction.
- No approval step and no notification beyond the in-app pills. If a
  freelancer needs to vet client files before they are "on the project", that
  is a new decision, not a patch to this one.
