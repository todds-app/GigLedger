# 0002. Keep the database in the repository root, derived once

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The database path was computed independently in two places, each hardcoding the
filename:

```python
# create_app()
base_dir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(base_dir, 'freelancecash.db')

# _migrate_db()
base_dir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(base_dir, 'freelancecash.db')
if not os.path.exists(db_path):
    return
```

Two properties made this fragile. First, a rename had to be applied correctly in
both places; miss the one in `_migrate_db()` and it silently returns early
against a file that does not exist, skipping every `ALTER TABLE` while the app
starts normally and appears healthy. Second, `base_dir` is derived from
`__file__`, so moving the code (as [0001](0001-declare-package-identity.md)
does) moves the database with it — the file would have landed inside the
`gigledger/` source package, where a careless `rm -rf gigledger/` takes the
user's data along with the code.

The database was renamed as part of adopting **GigLedger** as the canonical
name. The existing file contained only seeded demo data — a single
`demo@freelancecash.com` user and its generated clients, transactions, invoices,
projects and goals — so it was discarded and re-seeded rather than migrated. A
copy was taken first.

## Decision

Derive the path once, at module level, anchored to the repository root:

```python
DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(os.path.dirname(__file__))),
    'gigledger.db',
)
```

Both `create_app()` and `_migrate_db()` consume `DB_PATH`. The filename appears
in exactly one place.

An environment-variable override was considered and deferred. Nothing needs it
yet, and it is a one-line change if something does.

## Consequences

- The two call sites cannot disagree, so the silent-no-op migration failure is
  structurally impossible rather than merely unlikely.
- User data lives outside the importable source tree. `*.db` in `.gitignore`
  keeps it uncommitted; the redundant `freelancecash.db` entry was removed.
- The path is still relative to the code, not the working directory, so the app
  finds its database regardless of where it is launched from — the property the
  original derivation had, and worth keeping.
- `tests/test_smoke.py` asserts the resolved path is in the repository root and
  not inside the package, and that the app factory's configured URI matches
  `DB_PATH`.
- Deployments carrying real data would need a migration step for the rename.
  None existed here; that will not be true next time.
