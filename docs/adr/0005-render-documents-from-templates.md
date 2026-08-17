# 0005. Render export documents from templates; drop the WeasyPrint branch

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

The invoice and transaction exports were assembled as f-strings inside their
route functions — roughly 180 lines of HTML in Python, interpolating user data
with no escaping at 11 sites: business name, address and phone, client name,
company, email and address, line-item descriptions, invoice notes, transaction
descriptions and categories.

Both then ended in the same branch:

```python
try:
    from weasyprint import HTML as WeasyHTML
    pdf_bytes = WeasyHTML(string=html).write_pdf()
    return Response(pdf_bytes, mimetype='application/pdf', ...)
except ImportError:
    return Response(html, mimetype='text/html', ...)
```

An automated review rated this HIGH for SSRF and local file read: WeasyPrint
resolves URLs it finds in the document, so injected markup such as
`<img src="file:///etc/passwd">` becomes a file read.

**The rating was wrong for the code as it stood, and that matters.** WeasyPrint
was neither installed nor in `requirements.txt`, so the `ImportError` branch was
the only reachable path — verified against the running service, which returned
`text/html`. What actually shipped was an HTML injection into a file served as a
download, rendered from a `file://` origin with no session cookie to steal.

What made it worth fixing anyway is the shape of the latency: the vulnerability
**arms itself on `pip install weasyprint`**, with no code change and no warning.
A dormant trigger tied to an install command is a worse failure mode than a live
bug, because nothing marks the transition.

## Decision

**Render both documents from Jinja templates** — `templates/invoices/export.html`
and `templates/transactions/export.html` — so autoescape applies to every field
by default. Preferred over calling `html.escape()` at each of the 11 sites,
which preserves the property that caused the bug: the next field added is
unescaped unless someone remembers.

**Delete the WeasyPrint branch entirely** rather than adding the dependency with
a restricted `url_fetcher`. The app advertised PDF export, could not produce it,
and silently served HTML instead. Making the export honestly HTML removes the
trapdoor at zero cost. Adding WeasyPrint properly would mean native libraries
(cairo, pango, gdk-pixbuf) plus a fetcher rejecting `file://`, loopback,
link-local and RFC1918 — real work, justified only if genuine PDFs are wanted.
The UI and README were updated to say "printable HTML" instead of PDF; the
browser's own Print to PDF covers the use case.

## Consequences

- Escaping is now on by default in both documents, and every other page in the
  app already worked this way — this removes an inconsistency rather than adding
  a pattern.
- **Verified as an escaping-only change.** The rendered output of all 8 demo
  invoices and both transaction exports was captured before the change and
  compared after. Seven invoices were byte-identical; one differed by exactly
  one character class — `&` became `&amp;` in "Logo design & brand guide". With
  entities decoded, all ten documents are identical. There is no visual
  regression, and the one difference is the fix working.
- Confirmed live: a transaction described `<img src=x onerror=alert(1)>` and a
  business name of `<script>alert(document.cookie)</script>` both render as
  inert escaped text, with zero raw `<script>` in either export.
- `tests/test_no_handbuilt_html.py` walks the route modules with `ast` and fails
  on any f-string, `%`, `.format()` or concatenation that builds markup. Checked
  against the pre-fix code: it flags 9 offenders in `invoices.py` and 2 in
  `transactions.py`, and is clean on the new code — so it is not vacuous.
- The exports are no longer PDFs and never were in this deployment. Anyone
  relying on `application/pdf` from these routes gets `text/html`; the routes
  keep their `/pdf` paths to avoid breaking bookmarks, which is a small
  inconsistency accepted over a breaking URL change.
- If PDFs are wanted later, this decision should be superseded rather than
  patched: reintroducing WeasyPrint requires the `url_fetcher` work described
  above, and the template rendering here is a prerequisite for it either way.
