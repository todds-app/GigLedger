# 0015. Hours are time logs, counted by a timer on the project row

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

A project carried one number, `hours_logged`, and a form that added to it.
Nothing recorded when the hours were worked, so "hours this month" was
bucketed by the project's start date. The same form could book income, and
did so by posting the project's cumulative `earned` - every log after the
first booked the whole history again. A counter that is only ever added to
cannot tell an increment from a total.

People also wanted to time the work rather than estimate it afterwards, and
to bill a block of hours as one deliberate act rather than a checkbox on the
way past.

## Decision

**Hours are rows.** A `TimeLog` holds hours, the date range they were worked
over, and who logged them. `Project.hours_logged` is the sum of its logs; the
old column stays in an existing database, zeroed and undeclared, the way
ADR-0014 left the users' business columns. On the first start after this
change each project's old total becomes one log dated from the project's
start, so nothing already counted is lost.

**The timer lives on the project row.** `timer_started_at`, `timer_seconds`
(banked by earlier start/stop periods and not yet logged) and `timer_since`
(the first start of that unlogged stretch, which becomes the log's default
start date). State on the server means a reload, another browser or a restart
loses nothing, and the "one timer at a time" rule is a query, not a
convention: starting one project's timer stops whichever other one is running
and banks its seconds there. Leaving `active` stops the clock too.

**Logging is a confirmation, not a click.** Log Hours prefills from the timer
- seconds rounded to the nearest quarter hour, half up, never to zero once
started - and the posted figure is what is stored, rounded again by the same
rule. Logging never touches the ledger.

**Billing is a separate act and locks the log.** `TimeLog.transaction_id` is
optional and many-to-one: a log is billed on at most one transaction, and one
transaction may bill several logs. Create Transaction derives the amount
(`hours × rate`, hourly projects only), dates it when the hours ended, and
links the row. Bill all unbilled does the same for every unbilled log at
once - summed hours, dated when the latest block ended, every log linked to
the one row. While linked, hours cannot be edited and the log cannot be
deleted. Deleting the transaction nulls the link, which unlocks the log - the
ORM does that, the same mechanism that returns inventory to General Inventory
when its project goes.

*(Amended 2026-09-16: the link was 1:1 and unique at first; the constraint
went when billing several logs together arrived. An existing `time_logs`
table is rebuilt once without it on start.)*

## Consequences

**The dead column stays.** SQLite cannot drop it cleanly. It is not declared
on the model, so nothing reads or writes it after the backfill.

**Fixed-price progress still reads hours as a percentage.** `progress` was
`min(100, hours_logged)` before and is now; the hours it reads are real timer
hours. The formula is preserved, not endorsed.

**The ledger is not kept in step with the log.** Editing the transaction's
amount on the Transactions page does not change the hours, and deleting the
log (unbilled only) is the only way to remove hours. Edit one side by deleting
the transaction first, which unlocks the other.

**Daily and fixed projects log hours but do not bill them.** Their income
arrives by invoice or by hand. Offering `rate × hours / 8` for a day rate
would be a guess presented as a figure.

**`user_id` is stamped, never filtered.** ADR-0014's rule; here it happens to
mean "who logged the hours".

**The first stateful script.** The ticking display is the first JavaScript
in the app that holds state between renders. It holds only what the server
rendered plus the wall clock; every change of state is still a POST.
