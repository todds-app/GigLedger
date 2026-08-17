# 0003. Enforce CSRF protection app-wide with Flask-WTF

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The application had **no CSRF protection of any kind**. 37 routes accepted
state-changing POSTs — every transaction, invoice, client, project, goal,
recurring item and settings mutation — and none required a token. The only
defence was `SESSION_COOKIE_SAMESITE = 'Lax'`, set in `create_app()`, which
stops cross-site POSTs in current browsers but is a single point of failure:
it is a browser-side mitigation, not a server-side check, and it does nothing
for a same-site attacker or an older client.

This surfaced while assessing a stored-XSS finding in the projects template.
The XSS is only narrowly exploitable *because* all data is per-user and CSRF
would be needed to plant a payload in someone else's account — which made the
absence of CSRF protection the more load-bearing problem of the two.

Two facts made the retrofit much cheaper than it looked:

- **No AJAX.** There are zero `fetch`, `XMLHttpRequest`, `axios` or htmx
  request attributes in the templates. (htmx *is* loaded from a CDN in
  `base.html` but issues no requests.) Every mutation is a plain form submit,
  so no JavaScript needed rewiring to send tokens in headers.
- **No external consumers.** The README's "API Endpoints" table describes
  browser forms, not a public API, and several of its routes do not exist. No
  non-browser client can break.

`GET /logout` was also found: a state-changing route behind a safe method,
which CSRF protection does not cover by design. It was the only one — every
other mutating route already used POST.

## Decision

**Use Flask-WTF's `CSRFProtect`, initialised app-wide.** Protection is on by
default for every unsafe method; exemptions must be explicit. Rejected
hand-rolling it: the ~30 lines involved are security-critical, with a
synchronizer-versus-double-submit choice and a constant-time comparison in
them, and this app had no protection at all to fall back on.

**Inject the token via a `csrf_field()` template global**, registered in
`create_app()` and called once in each of the 48 POST forms. Chosen over
pasting the literal `<input>` 48 times (single source of truth for the markup)
and over an `after_request` HTML rewrite (invisible magic that fails silently —
the exact pattern [ADR-0001](0001-declare-package-identity.md) removed).

**Convert `/logout` to POST**, with the two nav links in `base.html` becoming
form-wrapped buttons.

**Leave `WTF_CSRF_SSL_STRICT` at its default (on).** See the trap below.

**Set a persistent `SECRET_KEY` via a gitignored `.env`.** CSRF tokens derive
from it. Note this file is `.env` and *not* `autostart.env`: the latter is
committed, so a key there would be published. `proj@.service` already sources
both.

## Consequences

- All 37 state-changing routes now require a valid token. `tests/test_csrf.py`
  measures this directly: **0 of 38 unsafe routes act on a token-less request
  with protection on; 38 of 38 do with it off.**
- The enforcement test discovers routes by walking `app.url_map`, so routes
  added later are covered automatically. Exemptions must be listed in
  `EXPECTED_EXEMPT`, making them visible in review.
- That test authenticates before probing, and a companion test
  (`test_the_probe_is_not_vacuous`) fails if it stops doing so. Without a
  login, 35 of 37 routes redirect to `/login` regardless of CSRF, and the
  enforcement test would pass with protection disabled — it was written that
  way first, and the vacuity check is what caught it.
- Tests run against a throwaway database rather than the real `gigledger.db`,
  because a *failing* enforcement run is one where routes do act, and those
  writes would land in real data.
- **Trap for later: `WTF_CSRF_SSL_STRICT` behind a proxy.** Flask-WTF also
  checks `Referer` against the host on HTTPS requests. The `XTransformPort`
  handling in `app.py` shows something in front of this app rewrites ports, and
  `SESSION_COOKIE_SECURE` is an opt-in TLS flag. If this is put behind a
  TLS-terminating proxy, legitimate submissions may start failing with no
  obvious cause. The fix is `werkzeug.middleware.proxy_fix.ProxyFix` so the app
  sees the real host — **not** disabling the check.
- A rejected submission flashes a message and redirects to a **fixed** endpoint.
  Deliberately not `request.referrer`, which would make the error handler an
  open redirect.
- The user loses whatever they typed when a token is stale. Accepted:
  preserving form state across a CSRF failure means re-rendering every form
  from POST data, which is a much larger change than this one.
