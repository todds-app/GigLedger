# 0004. Escape event-handler interpolations with `tojson` + `forceescape`

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

An automated review flagged one line in `templates/projects/index.html`:

```jinja
onclick="openEditModal({{ project.id }}, '{{ project.name | e }}', ...)"
```

The `| e` filter is not sufficient here, because the handler body is JavaScript
living inside an HTML attribute and therefore passes through two parsers. Jinja
escapes for the HTML one — `'` becomes `&#39;` — but the HTML parser *decodes*
entities before handing the attribute value to the JavaScript engine, so the
quote comes back and closes the string literal early:

    project name  x'); alert(document.cookie); //
    renders       onclick="openEditModal(1, 'x&#39;); alert(...); //', ...)"
    JS receives   openEditModal(1, 'x'); alert(...); //', ...)

**Scope was wider than reported.** The pattern appeared in **8 handlers across 5
templates** — projects, goals, recurring, transactions, and settings, where
`onsubmit="return confirm('Remove {{ cat }}?')"` interpolated a user-defined
category name. Cleared as safe: `{{ current_user.theme }}` in a `<script>` block
is allowlisted against `AVAILABLE_THEMES` on write, and the chart data already
used `| tojson` correctly.

**Exploitability is narrow but real.** All records are scoped by `user_id`, so
nobody else renders your project names; attacker and victim are the same person
unless combined with a CSRF path. That path is now closed by
[ADR-0003](0003-csrf-protection.md), which is why that work came first.

## Decision

**Use `| tojson | forceescape` for every interpolation inside an event handler.**
Both filters are required and neither is sufficient alone:

- `tojson` emits a complete JavaScript literal, quotes included, and escapes
  `<`, `>`, `&` and `'` as `\uXXXX`.
- `forceescape` then escapes the double quotes that `tojson` leaves **raw**.
  Without it the first `"` of the JSON string terminates the HTML attribute and
  silently breaks the markup. Because `tojson` output is already `Markup`, a
  plain `| e` is a no-op — `forceescape` is what actually applies.

Inside a `<script>` block bare `| tojson` remains correct, and `forceescape`
there would be wrong: there is no attribute to break out of. The existing chart
interpolations were left untouched.

Rejected the reviewer's suggestion of `data-*` attributes plus `dataset`. It is
architecturally cleaner, but it means rewriting 8 call sites *and* the 8
JavaScript function signatures they call, in frontend code with no test
coverage, to reach an identical security outcome. `tojson` is a one-filter
change per interpolation and matches the idiom already used elsewhere in these
templates.

**Enforce allowlists on constrained columns at the write.** `rate_type` against
`{hourly, fixed, daily}` and `color` against `^#[0-9a-fA-F]{6}$` — the previous
check accepted anything starting with `#`. The same treatment was extended to
`Goal.icon` and `Goal.color`, which had no validation at all; leaving them while
fixing the identical defect in projects would have been arbitrary. This is
defence in depth — the escaping fix closes the hole either way — but it also
protects `style="background: {{ goal.color }}"`, a CSS context the lint below
does not cover.

## Consequences

- `tests/test_template_escaping.py` walks every template and fails on any
  event-handler interpolation missing either filter, so handler nine cannot
  regress silently.
- The lint carries two guards against passing for the wrong reason: one asserts
  the templates directory is actually found, another that the handler regex
  matches at least 8 real interpolations. A parametrised property test then
  renders hostile payloads through **Flask's own Jinja environment** — not a
  bare one, since Flask installs its own `tojson` — and asserts the decoded
  JavaScript is still a single balanced literal.
- That property test earned its keep immediately: the first implementation of
  this fix used bare `| tojson`, which rendered raw `"` into every handler and
  broke the markup. The test caught it before it shipped.
- The lint covers event handlers only. Interpolation into `<script>` bodies,
  `style` attributes, and `href`/`src` have different rules and are not checked.
  `style` is currently safe only because of the colour allowlist above.
- Verified end to end against the running app: a project named
  `x'); alert(document.cookie); //` renders as `"x'); alert(...); //"` —
  inert data inside a string literal — and posting `rate_type=<script>` and a
  malformed colour stores `hourly` and `#34d399`.
