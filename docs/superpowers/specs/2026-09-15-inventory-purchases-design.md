# Inventory purchases and the asset pool

- **Date:** 2026-09-15
- **Status:** Approved, not yet implemented
- **Scope:** Piece 2 of 3 in the Inventory work. First visible change: an
  inventory purchase can be recorded, edited, listed and filtered.
- **Builds on:** `2026-08-29-transaction-kind-design.md` (piece 1), ADR-0010.

## Problem

Piece 1 made a third kind representable and proved that a `kind='inventory'`
row stays out of every cost total. Nothing can create one. Worse, the edit
modal branches `income / else-expense` in JavaScript, so if such a row ever
existed, changing only its date would post `type=expense` and move it into
every cost total permanently.

Piece 2 gives inventory an input path and a home. An inventory purchase is cash
out that buys an asset still owned: it needs a quantity, a unit cost, a flag
saying whether it is consumed or reused, and a place it was bought for — an
existing Project, or General Inventory.

## Decisions

### An inventory purchase is one ledger line plus one asset row

The purchase stays a `Transaction` (`kind='inventory'`, negative amount) so the
cash balance, the list, the filters and both exports handle it with no new
arithmetic. The asset facts live in a new table, one row per purchase:

```python
class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'
    id             = Integer, primary key
    user_id        = FK users.id, not null
    transaction_id = FK transactions.id, not null, unique   # the purchase
    project_id     = FK projects.id, nullable               # NULL = General Inventory
    quantity       = Float, not null                        # yards of fabric, not only chairs
    unit_cost      = Float, not null
    is_consumable  = Boolean, not null, default False       # False = reusable
    created_at     = DateTime
```

`Transaction.inventory_item` is a `uselist=False` relationship with
`cascade='all, delete-orphan'`: deleting the purchase deletes the item.
`InventoryItem.total_cost` returns `quantity * unit_cost`, so the pool never
reads the sign of an amount.

Description, category, date and amount stay on the Transaction. They are ledger
facts. Quantity, unit cost, consumable and project are pool facts. Piece 3's
movements table hangs off the item, which is where location, remaining quantity
and consumption belong.

**Why not per-kind nullable columns on `Transaction`** (the ADR-0006 pattern
`ProjectDocument` uses)? ADR-0006's argument was that an upload and a link
differ only in how content is fetched and every other operation is identical.
That is not true here. An item is placed, consumed and returned; a ledger line
never is. Different lifecycle, different table. See ADR-0011.

**One item per transaction.** A receipt with six chairs and two lamps is two
transactions. `amount` is always `-(quantity × unit_cost)`, so the ledger and
the pool cannot disagree, and a transaction's category is never ambiguous.

**General Inventory is `project_id IS NULL`.** No sentinel Project row per user
to seed, protect from deletion, or hide from the projects list.

### The sign rule

One rule for every kind, in `transactions.py` `add()` and `edit()`:

```python
amount = abs(amount) if kind == INCOME else -abs(amount)
```

This is the rule `recurring.py` already applies. The two files stop disagreeing.

For inventory the posted `amount` is ignored. The route validates `quantity > 0`
and `unit_cost > 0` (flash and redirect otherwise) and writes
`-(quantity × unit_cost)`. `is_tax_deductible` is forced `False` for inventory
whatever the form posts: the spec says not deductible, and a stray checkbox must
not make it so.

### Editing

- Same kind as stored, not inventory: unchanged behaviour.
- Inventory → inventory: description, category and date update on the
  Transaction; quantity, unit cost, consumable and project update on the item;
  amount is recomputed.
- **A kind change into or out of inventory is refused**, with the flash
  "Delete and re-add to change an inventory purchase into an expense, or an
  expense into an inventory purchase." Nothing changes. One guard clause, no
  create-or-orphan choreography, and piece 3 inherits a hard wall it would
  otherwise have to build: a placed item cannot quietly become an expense.

`project_id` is validated on add and edit: empty means NULL; otherwise it must
be one of the current user's projects, or the write is refused.

### Categories

`DEFAULT_INVENTORY_CATEGORIES`, the seven names from piece 1's appendix, sits
beside the other two default lists in `models.py`. A second constant,
`INVENTORY_CATEGORY_GUIDANCE`, maps each default name to its guidance string.
Guidance is shown, never stored.

`User` gains `custom_inventory_categories` (Text, default `''`) and
`get_inventory_categories()`, mirroring the existing pair.

`get_all_categories(kinds=KINDS)` becomes kind-aware: the union of the category
lists for the kinds given, order preserved, duplicates removed. The transactions
filter passes all three kinds so an inventory category can be filtered for. The
recurring page passes `{INCOME, EXPENSE}` so seven entries do not land in a
dropdown that cannot use them.

### Monthly commitment is a cash figure

Piece 1 left this undecided and it was resolved as cost by default. It is cash.
"Commitment" describes an obligation to pay, not a P&L category, and a recurring
inventory order is money that leaves the account every month. Both sums —
`dashboard.py` `monthly_commitment` and `recurring.py` `monthly_commitments` —
filter on `not r.is_income` instead of `r.is_expense`. ADR-0010's list of
deliberately kind-blind figures grows from two to three.

Nothing in this piece's UI can create a recurring inventory row (see Deferred).
The decision is made now so it is settled before one can.

## Migration

`db.create_all()` creates `inventory_items` on any database. `_migrate_db` adds
`users.custom_inventory_categories TEXT DEFAULT ''` when absent. Second run is a
no-op. No backfill: there are no inventory rows to backfill.

## User interface

### Transactions page

The add and edit modals gain a third radio, `inventory`, styled amber to match
the Monthly Commitments card. Choosing it:

- reveals the inventory block: quantity, unit cost, a consumable/reusable
  toggle, and a project select whose first option is "General Inventory";
- hides the amount input and the deductible checkbox;
- swaps the category list to the inventory categories and shows the guidance
  line under the picker, updating as the category changes.

`openEditModal` gains the four item fields and checks the radio by value rather
than `income / else`. Every `if (type === 'income') … else` in the page's
JavaScript becomes a lookup keyed on `type`.

The edit modal's markup and script move to a shared partial,
`transactions/_edit_modal.html`, so the Inventory page includes it rather than
carrying a copy.

The Type filter gains Inventory. `_filtered_transactions` already accepts it.

The HTML export gains a Type column. Without it an inventory row is
indistinguishable from an expense in a report whose totals exclude it. CSV
already has one.

### Dashboard quick-add: unchanged

Income and expense only. It is the fast path; an inventory purchase has four
more fields and belongs in the full modal. The quick-add's script is a copy of
the modal's and this piece does not grow the copy.

### Inventory page

New blueprint `inventory.py`, one route `index` at `/inventory`, nav entry after
Projects. **Read-only in this piece.** Every write goes through the transactions
routes, so money has one authorisation path and the page cannot drift from the
ledger.

- Three cards: **Asset value** (sum of `total_cost`), **On hand**
  (`project_id IS NULL`), **On projects** (the rest).
- Table: date, description, category, quantity, unit cost, total,
  consumable/reusable badge, location (project name with its colour dot, or
  "General Inventory"), Edit (opens the shared edit modal).
- Filters: project (including General Inventory) and category.
- No movements, no quantity remaining. Piece 3 owns those. The page shows what
  was bought and where it was assigned at purchase.

### Project detail

An "Inventory" section below Documents lists the items assigned to the project
with a total. Empty state is one line, not a card.

### Settings

A third block, "Inventory Categories", behaving exactly as the other two: add,
delete (defaults cannot be deleted), and `reset_categories` clears all three.
Routes `settings.add_inventory_category` and `settings.delete_inventory_category`.
Guidance text shows as a muted line under each default category so the taxonomy
is discoverable where it is managed.

## Deferred

**Recurring inventory.** The recurring form stays income/expense. A recurring
inventory purchase would need quantity, unit cost, consumable and project on the
recurring row and a generator that mints an item per run. Plausible for a
standing order of consumables; not needed to get the pool working. The
`RecurringTransaction.kind` column already holds `inventory` and the monthly
commitment rule already counts it, so adding the form later is additive.

**Quantity remaining, placement, consumption, cost recognition.** Piece 3.

## Testing

`tests/test_inventory.py`, following `tests/test_transaction_kind.py`.

**Sign rule.** Add and edit for each kind store the expected sign. An inventory
amount equals `-(quantity × unit_cost)` regardless of the posted amount.
Deductible is forced off. Non-positive quantity or unit cost is refused.

**The edit blocker, as a regression test.** Edit an inventory row changing only
its date. It is still inventory and still absent from every cost total. A kind
change into or out of inventory is refused and the row is unchanged.

**Item lifecycle.** Add creates exactly one item linked to the transaction.
Delete removes both. A `project_id` belonging to another user is refused.

**Monthly commitment.** A recurring inventory row, built directly as the
existing tests do, counts in both figures.

**Categories.** `get_all_categories()` filters by kind. The Settings block adds
and deletes; a default cannot be deleted; reset clears the third list. The
migration adds the column and is a no-op the second time.

**Pages.** The Inventory page shows the three stats correctly for a fixture with
items on hand and on a project, and filters by each. Project detail lists its
items. The Type filter offers Inventory. The HTML export shows a Type column.

**Characterization holds.** `test_finance_characterization.py` stays
byte-identical. `test_csrf.py` discovers the new POST routes by walking the
url_map and needs no edit.

## ADR

**ADR-0011, *Inventory purchases are ledger lines with a linked asset row*:**
the 1:1 table over per-kind columns, with the ADR-0006 contrast; amount as a
derived value; the refusal to change kind across the inventory boundary;
NULL project as General Inventory.

**ADR-0010** gains the monthly commitment figure in its kind-blind list.
