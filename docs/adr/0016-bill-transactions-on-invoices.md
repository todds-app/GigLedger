# 0016. The ledger is the income record; an invoice bills lines already in it

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Income was recorded twice. A freelancer who logged a payment in Transactions
and also raised an invoice for it saw the same money again when the invoice
was marked Paid: `update_status` posted a "Client Payment" income row for the
invoice total, plus a Tax Reserve expense. The two rows had no link to each
other, and nothing on the Transactions page said whether a line had been
billed at all.

The pieces to bill were already there. Income lines, and inventory purchases
to be re-billed to the client, are entered in Transactions; the invoice is
the document that asks for the money. ADR-0015 had set the shape for
"convert X into Y": an optional, unique 1:1 link, the source locked while
linked.

## Decision

**A line item may bill a transaction.** `InvoiceLineItem.transaction_id` is
ADR-0015's shape: 1:1, optional, unique. The link is on the line item and not
on `Transaction.invoice_id`, because that column already means "posted by
paying this invoice" and is what reverting or deleting an invoice deletes.
Reusing it would have deleted the user's own income rows.

**Add to Invoice offers a new draft or an existing one.** The Transactions
page shows an Invoiced column for income and inventory rows: the invoice
number and status once billed, otherwise an Add to Invoice button. The modal
creates a new draft (client optional, next number, due in 30 days) or appends
to a draft picked from a list; only drafts take lines. Income bills as one
unit at its amount; an inventory purchase bills as `quantity × unit_cost`
so the client sees the pieces. Every change to the lines goes through
`Invoice.recalculate`, because the totals are stored and the PDF reads them.

**Locked while invoiced.** An invoiced transaction cannot be edited or
deleted. It unlocks when its line is removed from the draft (a new action on
the invoice page, drafts only) or when the invoice is deleted - the cascade
removes the line, and with it the link.

**Paid posts no income.** Marking an invoice Paid still sets `paid_date` and
posts the Tax Reserve expense; it no longer posts a Client Payment row. The
demo seed does the same. Income rows that an earlier version posted
(`source='invoice'`, kind income) are hidden from the Transactions page and
its exports.

## Consequences

**Legacy Client Payment rows still count in reports.** They are hidden, not
deleted: deleting a user's income history on upgrade is not the migration's
call. `finance.py` and Reports still read them, so a database that relied on
invoices for its income record keeps its totals until those rows are removed
by hand.

**A draft is the only editable invoice.** The create form doubles as the
edit form for a draft: client, dates, notes and lines can all change until
it is sent. Sent and later invoices are fixed, which is what a sent bill
should be.

**The line is the invoice's own.** Description, quantity and rate are
copied from the transaction when the line is made and may then be edited on
the draft - marking up an inventory purchase is the usual reason. The link
survives the edit, so the transaction stays locked; taking the row off the
form deletes the line and unlocks it. Editing the transaction itself is
refused while linked, so the ledger side cannot drift underneath the bill.

**Every ledger line still has one meaning.** `source='invoice'` continues to
mean "posted by paying an invoice"; a billed transaction keeps whatever
source it was entered with.
