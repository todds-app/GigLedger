# Architecture Decision Records

One file per decision, named `NNNN-short-slug.md`, numbered in the order the
decisions were made. An ADR records *why* a choice was made, so that a future
reader can tell a deliberate decision from an accident.

An ADR is written when a decision is **settled**, not while it is being debated.

## Format

```markdown
# NNNN. Title stated as the decision

- **Status:** Proposed | Accepted | Superseded by [NNNN](NNNN-slug.md)
- **Date:** YYYY-MM-DD

## Context

The situation that forced a decision. Facts, constraints, and what actually went
wrong — including anything that turned out to be a misdiagnosis.

## Decision

What was chosen, in the active voice: "We derive X from Y."

## Consequences

What this makes easy, what it makes hard, and what now has to stay true. Include
the failure modes the decision does *not* protect against.
```

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-declare-package-identity.md) | Declare package identity instead of deriving it from the filesystem | Accepted |
| [0002](0002-database-path.md) | Keep the database in the repository root, derived once | Accepted |
