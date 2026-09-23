# 0017. A sale is the invoice line that bills the purchase; paying posts the cost back and the profit

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

An inventory item is bought as an asset (ADR-0011): cash out, not a cost.
Nothing recorded that it was later sold to a client. The only path was
ADR-0016's markup - put the purchase on an invoice and edit the line's rate
by hand - which left no sale price, no sales tax, no profit and no change to
the asset pool. The item stayed in "asset value" for ever, and the money the
client paid for it never reached the ledger at all, because Paid posts no
income.

Three facts about a sale needed a home: what it sold for, whether sales tax
was collected and how much, and the profit or loss against its cost. And the
sale had to appear on an invoice, because that is how a client is asked to
pay.

## Decision

**The sale is the invoice line.** `InvoiceLineItem.transaction_id` already
links a line to the purchase it bills, 1:1 and optional (ADR-0016). That link
now *means* sold: the line's amount is the sale price, and a new
`InvoiceLineItem.sales_tax` is what was collected on top. `Invoice.sales_tax`
is the sum, denormalised like the other totals, and `total` includes it. No
separate sale table: removing the line or deleting the invoice un-sells the
item, exactly as it already unlocked the purchase.

**Selling is Add to Invoice with a price.** The same route,
`POST /transactions/<id>/invoice`, requires `sale_price` for an inventory
purchase and reads `sales_tax` when "collected" is ticked. The line bills
`quantity × (price ÷ quantity)`, so the client still sees the pieces. The
Sell modal is offered on the Inventory page, the project's inventory list
and the Transactions page; it prefills the price with the cost and the
client with the item's project's client. Whole item only: partial quantities
wait for movements (piece 3).

**Paying posts the cost back and the difference.** For every line that
sells an item, marking the invoice Paid posts a positive inventory-kind row
for the cost basis (the asset has left the pool) and then the profit as
income under `Inventory Sales`, or a shortfall as a deductible expense under
the same name, or nothing at cost. Revenue therefore shows the profit, not
the sale price. The rows carry `invoice_id`, so the existing revert and
delete paths remove them, and `source='sale'` - not `'invoice'`, whose
income rows the Transactions page hides as ADR-0016's legacy Client Payments.

**Sales tax never enters the ledger.** It is the client's money passing
through to the state. It sits on the line and in the invoice total, and the
Invoices page and Reports total it over paid invoices as "Sales tax
collected (to remit)". Revenue, profit and safe-to-spend never see it, which
is the reserve: money that is never counted cannot be spent.

**Rows posted by paying an invoice are locked.** `Transaction.invoice_id`
set means edit and delete are refused with "revert the invoice". This now
covers the Tax Reserve row too, which was editable before; a reserve edited
by hand would leave the invoice claiming a set-aside the ledger no longer
holds.

## Consequences

**The first positive inventory row.** ADR-0011's sign rule - income is cash
in, every other kind cash out - still governs every row a form posts;
`_signed` is untouched. The cost-recovery row is derived by the route, never
typed, and is positive on purpose: the purchase was `-cost`, the sale
returns it, and the pool nets to zero. Cash-blind sums (`safe_to_spend`,
`runway`) net `-cost + cost + profit`, which is the cash truth.

**Sold means on an invoice, not paid.** The Inventory cards drop an item
the moment it is on a draft. The invoice, not the pool, is what tracks
whether the money has arrived; until Paid the ledger still holds only the
purchase.

**The invoice's own tax still applies to the sale line.** The "Tax (N%)"
line is `Business.default_tax_rate`, the self-employment reserve rate,
applied to the subtotal. A sold item's price feeds that subtotal like any
other line. That is a pre-existing choice this decision does not revisit.

**Existing databases.** A line that already billed a purchase becomes a sale
at its current amount with no tax. An invoice paid before this change posts
nothing retroactively; reverting it and marking it Paid again would.

**`Inventory Sales` is a locked category** for income and for expense, the
same wall as Tax Reserve and for the same reason: the app files into it by
name.
