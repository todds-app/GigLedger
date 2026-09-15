# 0011. Inventory purchases are ledger lines with a linked asset row

- **Status:** Accepted
- **Date:** 2026-09-15

## Context

ADR-0010 made `inventory` a representable kind and kept it out of every cost
total. Nothing could create one. An inventory purchase also carries facts a
ledger line does not: a quantity, a unit cost, whether the item is consumed or
reused, and what it was bought for - a project, or stock held for the next
job. Piece 3 will add movements: an item placed on a project, used up,
returned. Those facts and that lifecycle needed a home.

Two shapes were open. Nullable per-kind columns on `Transaction`, the pattern
ADR-0006 chose for `ProjectDocument`. Or a separate table, one row per
purchase, linked back to it.

## Decision

A separate table, `inventory_items`, 1:1 with its purchase `Transaction`
(`transaction_id` is unique). The transaction keeps description, category,
date and amount - ledger facts. The item holds quantity, unit cost,
`is_consumable`, and `project_id`, where NULL means General Inventory.

`amount` is derived: always `-(quantity × unit_cost)`, written by the route,
never posted by the form. The ledger and the pool cannot disagree.

Every write goes through `transactions.py`. The `/inventory` page and the
project detail section are read-only views over the ledger.

A kind change into or out of `inventory` by editing is refused. Delete and
re-add.

## Consequences

**Why not ADR-0006's shape.** That ADR's argument was that an upload and a
link differ only in how content is fetched and every other operation -
listing, sharing, deleting, authorising - is identical, so one table with a
discriminator beats two tables and two copies of the authorisation check. An
inventory item is placed, consumed and returned; a ledger line never is.
Different lifecycle, different table. Piece 3's `inventory_movements` will
reference an item, which reads correctly; `movements.transaction_id` would
not.

**One item per transaction.** A receipt with six chairs and two lamps is two
transactions. The alternative - line items under one transaction, like
`Invoice → InvoiceLineItem` - would make `amount` a sum, the edit modal a
line-item editor, and a transaction's category ambiguous when its items span
categories.

**The refused kind change is a wall, not a convenience.** Allowing it means
creating or orphaning an item mid-edit. Refusing it costs one guard clause
and gives piece 3 a guarantee it would otherwise have to build: a placed item
cannot quietly become an expense.

**Deleting a project returns its items to General Inventory.** The
`Project → inventory_items` relationship has no delete cascade; SQLAlchemy
nulls the foreign key. Assets that are still owned are not destroyed with the
job they were bought for.

**One sign rule.** `transactions.py` now applies the rule `recurring.py`
already did: income is cash in, every other kind is cash out. The two-branch
coercion it replaced left a third kind's sign to whatever was posted.

**Monthly commitment is cash.** Recorded in ADR-0010's kind-blind list.

**Recurring inventory is deferred.** The recurring form offers income and
expense. `RecurringTransaction.kind` already holds `inventory` and the
commitment rule already counts it, so adding the form later is additive.

**The dashboard quick-add offers two kinds.** It is the fast path; an
inventory purchase has four more fields. Its script is a copy of the
transactions modal's, and this decision declines to grow the copy.
