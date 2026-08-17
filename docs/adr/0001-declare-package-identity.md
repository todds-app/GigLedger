# 0001. Declare package identity instead of deriving it from the filesystem

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The application package was the repository root directory itself. Modules inside
it used relative imports (`from .models import db`), which requires the package
to be imported under *some* absolute name, so `run.py` put the parent directory
on `sys.path` and imported the package by a hardcoded name:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from freelancecash.app import create_app
```

That name was `freelancecash`, but the checkout is named `GigLedger`. The
importable name of the application was therefore a consequence of what the
checkout folder happened to be called — a fact about the filesystem, not
something the project declared or could verify.

This is downstream of an unfinished rename inherited from upstream commit
`8627fa3`, which converted `models.py`, `routes/projects.py`,
`routes/invoices.py`, and `routes/reports.py` to `GigLedger` and left `app.py`,
`finance.py`, `run.py`, `__init__.py`, `package.json`, the templates, and the
database filename on `FreelanceCash`.

**What actually failed.** The mismatch made `run.py` raise
`ModuleNotFoundError: No module named 'freelancecash'` at startup. A comment
added alongside an earlier fix described the failure differently — that it
"loaded a *sibling* clone: wrong code against the wrong database." That
diagnosis is not supported: no `freelancecash` directory or symlink exists, and
the import can only crash. The belief has a traceable origin, though — the
README instructed anyone with a differently-named folder to run
`ln -s "$(pwd)" ../freelancecash`. Had that symlink existed, the same code would
have been importable under two module names, producing two SQLAlchemy registries
against one database file. That is a real hazard, and it is a hazard the symlink
workaround *creates*.

Two fixes were considered and rejected:

- **Keep the symlink workaround.** It makes the constraint someone else's
  problem and introduces the dual-registry hazard described above.
- **Derive the name from the directory** (`_PKG = os.path.basename(_HERE)`,
  then `__import__(f"{_PKG}.app", ...)`). This works, and it is consistent with
  the `proj@.service` / `proj-run` deployment convention, which already keys on
  the directory name. But it converts a loud crash into a silent inference: copy
  or rename the checkout and the import target follows without complaint.

## Decision

Move the application into a `gigledger/` subpackage inside the repository, and
leave `run.py` at the repository root importing it by its declared name:

```python
from gigledger.app import create_app
```

No `sys.path` manipulation is required, because Python already places a script's
own directory at `sys.path[0]`. The checkout folder may be named anything.

The canonical name is **GigLedger**, chosen because every external dependant —
the repository, both git remotes, and the `proj@GigLedger.service` systemd unit
— already uses it, while `FreelanceCash` survived only in internal strings.

## Consequences

- The failure class is gone rather than worked around. Package identity is a
  directory the project owns and version-controls, and it cannot disagree with
  the filesystem because it no longer consults it.
- The fix is a net deletion: the `sys.path.insert` and both `__import__` calls
  are removed.
- The README's installation instructions were wrong in a harmful direction —
  they told readers to create the symlink — and are corrected in the same
  change.
- The directory name remains load-bearing for *deployment*: `proj@GigLedger.service`
  resolves `%i` to `~/projects/GigLedger`, and `autostart.env` hardcodes an
  absolute interpreter path. Renaming the checkout still requires updating both.
  This decision decouples Python from the directory name, not systemd.
- `tests/test_smoke.py` asserts the package imports under its declared name, so
  a regression fails in CI rather than at launch.
- This diverges structurally from `Cyber-Dioxide/GigLedger`. Acceptable: this is
  a hard fork with no planned upstream merges.
