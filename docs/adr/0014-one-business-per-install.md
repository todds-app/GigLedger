# 0014. One business per install

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

GigLedger was written for one freelancer: every row carried a `user_id`, and
every route filtered on `user_id = current_user.id`, so two logins were two
businesses that could not see each other. The install this was built for is
one interior-design practice — a designer and perhaps an assistant or two —
and every one of them needs the same view of the same books.

The alternatives were a `Business` with members (multi-tenant, one more level
of scoping on every table) or a shared owner pointer (every admin filters on
one canonical user's id). Both keep machinery for a capability nobody asked
for; the second also makes "user" mean two things.

## Decision

One install keeps books for one business.

- A single-row `Business` holds the settings that are business-wide: name,
  address, phone, tax rate, currency, invoice prefix and counter, invoice note,
  categories. `Business.get()` is the one seam that fetches it.
- `User` is a login plus personal preferences (theme, dark mode).
- `@login_required` is the whole access rule on the admin side. No query
  filters on `user_id` for visibility.
- `user_id` columns stay and are stamped on new rows, as a record of who
  created a row. Nothing reads them for access, no UI shows them, and nothing
  cascades from `User` into them: removing an admin leaves their rows.
- Admins are added by invitation from Settings (same token rules as portal
  invites: returned once, stored hashed, 7-day TTL). Public sign-up is gone.
  A fresh install creates its first admin at `/setup`, which 404s once any
  admin exists.
- The demo seed runs only with `SEED_DEMO=1`; it creates an admin with a
  public password, which on a one-business install would see everything.

## Consequences

- ADR-0008's premise that ~40 routes scope rows by `user_id` is superseded.
  Its conclusion stands for a different reason: a portal account must never
  be `current_user` because `current_user` is an admin, and an admin sees
  everything. A portal session and an admin session remain mutually exclusive.
- The Client Portal's explicit-grant rule (ADR-0009) is now the only place in
  the app where two principals may see different rows.
- The portal home is headed by the business name and grouped by project;
  there is no longer an owner to group by.
- The security story on the admin side is simpler and must be stated as
  such: anyone with an admin login sees the whole business. Invitations are
  therefore the access-control decision, and the Team panel is where it is
  made and undone.
- Old business columns on `users` remain in existing databases, unread.
- Removing an admin deletes only the `users` row; `user_id` values on other
  tables and `admin_invites.invited_by` are left pointing at a deleted id.
  This is safe only because the app never enables SQLite foreign-key
  enforcement (`PRAGMA foreign_keys` is never set) and no query dereferences
  those rows' `.user` backref. Enabling FK enforcement, or moving to another
  database, would require either nulling those columns on removal or a
  different design.
