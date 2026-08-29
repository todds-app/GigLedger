# Explicit transaction kind

- **Date:** 2026-08-29
- **Status:** Approved, not yet implemented
- **Scope:** Piece 1 of 3 in the Inventory work. Foundation only; no visible change.

## Problem

`Transaction` has no column saying what kind of transaction it is. The kind is
inferred from the sign of `amount` — `'Income' if tx.amount > 0 else 'Expense'` —
in roughly twenty-five places across `finance.py`, `reports.py`, `dashboard.py`,
`transactions.py`, `recurring.py` and both exports.

A sign carries one bit. It can express two kinds and no more, so a third kind is
not something the current representation can hold. Inventory is that third kind,
and it must behave unlike either existing one: an inventory purchase is cash
leaving the bank that is *not* an expense, because the money buys an asset that
is still owned. Under sign-derived classification every expense total in the app
would silently absorb it.

This spec covers only the representation change. Income and expense behave
exactly as they do today; the deliverable is that a third kind stops being
unrepresentable.

## Decision

Add an explicit `kind` column and give it one place that owns its meaning.
Amounts stay signed — every `sum(amount)` in the app keeps working — and `kind`
becomes authoritative for classification.

### Vocabulary

`Transaction.kind`, `String(20)`, not null, one of:

| Kind | Cash | Counts as expense | Reduces profit | Deductible |
|---|---|---|---|---|
| `income` | in | no | no (increases) | n/a |
| `expense` | out | yes | yes | if flagged |
| `inventory` | out | **no** | **no** | no |

All three are introduced now, including `inventory`, even though nothing creates
an inventory row until piece 2. Introducing the full vocabulary here is what
makes piece 2 additive rather than a second sweep of the same twenty-five sites.

`RecurringTransaction` gets the same column. It generates transactions in
`recurring.py:219`, so it must carry a kind forward rather than have the
generator re-derive one from a sign.

### The seam

One place owns what a kind means. Sites ask the seam; no site compares an amount
to zero to decide what something is.

In `models.py`, beside the existing category constants:

```python
INCOME, EXPENSE, INVENTORY = 'income', 'expense', 'inventory'
KINDS = {INCOME, EXPENSE, INVENTORY}

# Kinds that reduce profit. Inventory is cash out but not a cost: the money
# bought an asset that is still owned. See ADR-0010.
COST_KINDS = {EXPENSE}
```

`clean_kind(value, fallback=EXPENSE)` follows the Constrained Column convention
already used by `clean_rate_type` and `clean_color` in `routes/projects.py:26-33`:
a value off the allowed set is replaced by the fallback at the write, so the
column cannot hold a kind the rest of the code has never heard of.

On `Transaction`: `is_income`, `is_expense`, `is_inventory` properties.

`finance._get_tx_range` widens its tuple from `(amount, is_tax_deductible)` to
`(amount, is_tax_deductible, kind)`. It is the single funnel feeding the monthly
summary, the quarterly income/deductions calculation and the dashboard's
deductible figure, so widening it converts three aggregations at their source.

## Migration

`db.create_all()` at `app.py:184` creates missing *tables*. It never alters an
existing one, so a `transactions` table that predates the column will not gain
it. This project has no migration tooling, but it does already have
`_migrate_db` at `app.py:190` — a `PRAGMA table_info` → `ALTER TABLE` helper
that already migrates the transactions table for `invoice_id`, and commits at
the end. The backfill joins it there rather than arriving as a second
mechanism.

Added to the existing transactions block:

1. `PRAGMA table_info(transactions)`; if `kind` is present, do nothing.
2. `ALTER TABLE transactions ADD COLUMN kind VARCHAR(20)`
3. `UPDATE transactions SET kind = CASE WHEN amount > 0 THEN 'income' ELSE 'expense' END`

Then the same three steps for `recurring_transactions`, which `_migrate_db`
does not touch today.

Running it on every startup is a no-op after the first, which is the property to
test rather than assert.

### Zero-amount rows

The `ELSE` branch backfills a zero-amount transaction as `expense`. Today such a
row counts as neither — both sign tests are strict `> 0` and `< 0` — so this is
a behaviour change on paper. It is not one in practice: a zero contributes zero
to an expense total, and every number the app displays is unchanged. Pinned by a
test rather than left to a comment.

## Conversion checklist

Every site that currently classifies by sign. The refactor is complete when none
of these compares an amount to zero to decide what a transaction is.

**`finance.py`** — the three aggregations convert at `_get_tx_range`:

- `calculate_monthly_summary:50-51` — income/expenses by kind
- `calculate_quarterly_income_deductions:88-89` — income, and deductions from
  `COST_KINDS` only
- `get_category_breakdown:185` — `Transaction.amount < 0` becomes
  `Transaction.kind == EXPENSE` in the SQL filter

**`routes/dashboard.py`**

- `:47` — `deductible_this_month`, now kind-aware via the widened tuple
- `:69` — `monthly_commitment` reads `RecurringTransaction.kind`, not sign

**`routes/reports.py`**

- `:46`, `:47`, `:49` — yearly income, expenses, deductible
- `:102` — the top-5 expense-categories query filter
- `:123`, `:124` — quarterly income and expenses

The monthly loop at `:60-73` delegates to `calculate_monthly_summary` and
converts for free. `:77` (`m['income'] > 0`) reads a computed total, not a
transaction, and stays as it is.

**`routes/transactions.py`**

- `:23-26` — list filter
- `:57-59`, `:99-101` — add/edit sign coercion, now driven by `clean_kind`
- `:75` — the deductible flash message
- `:147-150`, `:179-181` — CSV export filter and totals
- `:171` — the `'Income' if tx.amount > 0 else 'Expense'` column label
- `:218-221`, `:236-238` — PDF export filter and totals

**`routes/recurring.py`**

- `:44` — monthly commitments sum
- `:59`, `:76`, `:131-132` — sign coercion on add and edit

**Templates** — these classify by sign too, which the checklist first missed.
Only the *type labels* convert; `+`/`-` and red/green describe the amount and
stay sign tests.

- `templates/transactions/index.html:131` — the type badge
- `templates/transactions/index.html:152` — the deductible/income branch
- `templates/transactions/index.html:161` — the kind argument passed to
  `openEditModal`, which decides the pre-selected radio button
- `templates/recurring/index.html:72`, `:112` — the same badge and modal argument

Left alone as direction rather than classification:
`templates/transactions/index.html:127`, `:135-136`, `:139`, `:149`;
`templates/recurring/index.html:60`, `:68-69`;
`templates/dashboard/index.html:330-343`; `templates/transactions/export.html:56`.

**Creation sites — eight, each sets `kind` explicitly**

| Site | Kind |
|---|---|
| `routes/transactions.py:65` | from `clean_kind(form['type'])` |
| `routes/invoices.py:185` | `income` |
| `routes/invoices.py:199` | `expense` (the tax counterpart) |
| `routes/projects.py:197` | `income` (hours logged) |
| `routes/recurring.py:219` | inherited from `rt.kind` |
| `app.py:388`, `:394` | `income`, `expense` |
| `app.py:436`, `:450`, `:464` | per seed block |

### Deliberately unchanged

The bank-balance sums in `calculate_safe_to_spend:63-66` and
`calculate_runway:127-130` stay `sum(amount)` across all kinds. Those measure
cash, and inventory cash really does leave the account. This is the design, not
an oversight — recorded in ADR-0010 so it is not later "fixed" into consistency
with the expense totals.

## User interface

Nothing visible changes. The add and edit modals keep posting
`type=income|expense`; the route maps that through `clean_kind`. The filter
dropdown keeps its two options. The Inventory option arrives in piece 2.

## Testing

**Characterization first.** Before touching any logic, pin the current output of
a fixture ledger with known amounts across: monthly summary, quarterly tax,
runway, category breakdown, the dashboard deductible figure, and both exports.
These must be byte-identical after the refactor. This is the safety net for a
change whose whole promise is "nothing moves".

**Migration.** A database built without the column gains it; the backfill
matches sign; a second startup is a no-op; zero-amount rows land in `expense`
and leave every displayed total unchanged.

**Creation sites.** Every path that writes a `Transaction` sets a kind — no row
is ever written with a null or unrecognised kind.

**The foundation test.** An inventory transaction constructed directly in a test
is absent from expenses, deductions and profit, and present in the cash balance.
Nothing in the UI can produce that row yet, which is exactly why the test must
exist: it is the evidence that piece 2 has a foundation, and it fails loudly if
a later change lets inventory leak into a cost total.

**Drift.** `tests/test_csrf.py` discovers routes by walking the url_map and
needs no edit. No new routes are added by this piece.

## ADR

ADR-0010, *Classify transactions by an explicit kind, not the sign of the
amount*, recording the one-bit argument, the cash-versus-cost distinction, and
the deliberate exemption of the balance sums.

---

## Appendix: settled decisions for pieces 2 and 3

Recorded here so the reasoning is not lost between specs. Neither piece is
designed yet.

**Piece 2 — Inventory purchases and the asset pool.** An inventory purchase is
held as an asset and does not reduce profit when bought. Each purchase is
assigned to an existing Project or to General Inventory. A row carries a
quantity and a unit cost, and a flag marking it consumable or reusable.

**Piece 3 — Movements and cost recognition.** Consumables become a cost when
used on a project; reusable stock is placed on a project and returns to General
Inventory for the next job, staying an asset throughout. Cost recognition emits
a real `expense` transaction linked back to the inventory row, so every existing
report, tax estimate and export handles project costs with no new arithmetic —
which is why `COST_KINDS` stays `{EXPENSE}`.

Quantity plus partial placement means an item's location is a distribution, not
a field — four of six chairs at one project, two on hand. That implies a
movements table.

**Categories.** Seven, flat in the data. The subcategory wording below is
guidance shown under the picker so people choose consistently; it is not stored.

| Category | Description | Guidance shown |
|---|---|---|
| Casegoods & Storage | Non-upholstered hard furniture built for storage and display | Beds, nightstands, dressers, chests, armoires; TV stands, media consoles, bookcases, shelving, sideboards, credenzas; dining tables, buffets, desks, filing cabinets |
| Seating | Seating and soft furnishings built with fabric, leather, padding and frames | Sofas, sectionals, loveseats, accent chairs, recliners, ottomans, benches; upholstered dining chairs, executive desk chairs |
| Lighting | Fixtures for ambient, task and accent illumination | Chandeliers, pendants, flush mounts, track lighting; table, floor and desk lamps; wall sconces, vanity lights, picture lights |
| Soft Goods & Textiles | Fabrics and flexible materials adding warmth, texture and comfort | Curtains, drapes, blinds, shades and hardware; area rugs, runners, doormats, rug pads; sheets, comforters, duvet covers, pillows, bath towels, shower curtains; throw pillows, poufs, blankets |
| Wall Decor & Art | Vertical ornamentation used to personalise surfaces | Framed canvas prints, paintings, photographic prints, wall sculptures; mirrors, wall clocks, floating shelves |
| Tabletop & Decorative Accessories | Finishing touches and surface accent pieces | Vases, sculptures, decorative bowls, trays, candles and holders, picture frames; faux plants, dried florals, planters, pots; dinnerware, glassware, flatware, serveware, table linens |
| Outdoor & Patio | Furniture and accessories built for exterior spaces | Outdoor seating, dining sets, fire pits, outdoor rugs, weather-resistant lighting |

Large appliances and electronics — a washer, a fridge, a television — have no
category. The taxonomy classifies by construction and function, and these fit
none of the seven. Left as a known gap rather than forced into
`Tabletop & Decorative Accessories`.

Storage: `DEFAULT_INVENTORY_CATEGORIES` alongside the two existing lists at
`models.py:12-13`, a `custom_inventory_categories` column, and a third block in
the Settings UI. `get_all_categories()` currently merges income and expense for
the transactions filter dropdown; it becomes kind-aware so seven more entries do
not land in that filter unasked.

---

## Appendix: what piece 1 left for piece 2

Found during implementation and review of piece 1, recorded here because the
execution workspace does not survive. Piece 1 is complete and correct on its own
terms — none of these affect income or expense behaviour, and no inventory row
can be created yet. All of them bite the moment one can.

**Blockers — piece 2 cannot ship without these.**

*The edit modal destroys an inventory row.* `openEditModal` receives the row's
real kind, but the JavaScript branches `if (type === 'income') … else` and checks
the expense radio for anything else. Editing an inventory transaction — changing
only its date — posts `type=expense` and permanently moves it into every cost
total. `edit()` falls back to the stored kind when `type` is absent or
unrecognised, but the modal sends `expense` explicitly, so that guard never
fires. The fix is the third radio button.

*Inventory has no input path.* `transactions.py`'s `add()` and `edit()` coerce
the sign for `income` and `expense` only, so an inventory row keeps whatever sign
was submitted. `recurring.py` was fixed to the rule "non-income is cash out";
these two were left because the UI cannot reach them. Piece 2 should apply the
same rule when it adds the input path.

*The Type filter offers only Income and Expense.* `_filtered_transactions`
accepts any member of `KINDS`, so the backend is ready; the dropdown is not.
Inventory rows cannot be filtered for.

**Undecided — needs a deliberate call.**

*Is "monthly commitment" cash or cost?* `dashboard.py` and `recurring.py` filter
it on `is_expense`, so a recurring inventory purchase is excluded from a figure
labelled as a cash commitment. ADR-0010 exempts only the two balance sums from
the cost rule; this third cash-flavoured figure was resolved the other way
without anyone deciding it. Choose, and record the choice.

**Known limitations, accepted.**

*The HTML/PDF export has no Type column.* CSV has one. An inventory row in the
HTML export is indistinguishable from an expense while being excluded from that
report's own totals, so its rows will not sum to its summary.

*A NULL kind is representable on a migrated database.* See ADR-0010's closing
paragraph. `edit()` now preserves a NULL rather than inventing `income` for it,
which means the edit path is not a repair path either. Repair, if ever needed, is
a deliberate migration.
