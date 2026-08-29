# 0010. Classify transactions by an explicit kind, not the sign of the amount

- **Status:** Accepted
- **Date:** 2026-08-29

## Context

`Transaction` had no column saying what kind of transaction it was. The kind was
inferred from the sign of `amount` - `'Income' if tx.amount > 0 else 'Expense'` -
in roughly twenty-five places across the aggregations, the reports, both exports
and the templates.

A sign carries one bit. It expresses two kinds and no more, so a third kind was
not something the representation could hold. Inventory is that third kind, and
it behaves like neither existing one: an inventory purchase is cash leaving the
bank that is *not* an expense, because the money bought an asset that is still
owned. Every expense total in the app would have absorbed it silently.

## Decision

We store the kind: `income`, `expense`, or `inventory`, on both `Transaction`
and `RecurringTransaction`. Amounts stay signed, so the sign says which way the
money moved and the kind says what the movement was.

One seam in `models.py` owns the vocabulary. `COST_KINDS` answers "does this
reduce profit" in a single place, and inventory's absence from that set is the
entire behaviour. `clean_kind` constrains the column at the write, as
`clean_rate_type` and `clean_color` do for theirs.

Existing databases are backfilled from the sign by `_migrate_db`, which already
handles this table. `db.create_all()` creates missing tables and never alters an
existing one, so the column would otherwise never appear.

## Consequences

A fourth kind is now a change to one set and the sites that name it, rather than
a survey of every comparison against zero.

**The two balance sums stay kind-blind on purpose.** `calculate_safe_to_spend`
and `calculate_runway` sum every kind, because they measure cash and inventory
cash really does leave the account. This looks like a missed conversion and is
not one. Do not "fix" it.

Templates read the sign for two purposes and only one was classification. The
`+` and the red-green colouring describe the amount and remain sign tests; the
type badge and the edit-modal argument read the kind.

Backfilling by sign puts zero-amount rows in `expense`. They counted as neither
before, because both sign tests were strict. No displayed figure moves, since a
zero contributes zero to an expense total.

This does not protect against a kind written outside `clean_kind` - a direct
`db.session.add` with a typo'd string still lands in the column. The seeded and
route-level writes are covered by tests; a future writer is not.
