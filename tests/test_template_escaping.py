"""Template escaping lint.

Guards the bug class fixed in docs/adr/0004: template data interpolated into an
inline event-handler attribute.

Why `| e` is not enough there. The handler body is JavaScript living inside an
HTML attribute, so it passes through two parsers. Jinja's `e` filter escapes for
the HTML one - a `'` becomes `&#39;` - but the HTML parser *decodes* entities
before handing the attribute value to the JavaScript engine, so the quote comes
back and closes the string literal early:

    onclick="fn('{{ name | e }}')"   with name = x'); alert(1); //
    renders   onclick="fn('x&#39;); alert(1); //')"
    JS sees   fn('x'); alert(1); //')

The fix needs BOTH filters:

* `| tojson` emits a complete JavaScript literal - quotes included - and escapes
  `<`, `>`, `&` and `'` as `\\uXXXX`.
* `| forceescape` then escapes the double quotes that tojson leaves raw. Without
  it the first `"` of the JSON string *terminates the HTML attribute*, silently
  breaking the markup. tojson output is Markup (already safe), so plain `| e` is
  a no-op here and `forceescape` is required.

Inside a `<script>` block bare `| tojson` is correct and `forceescape` would be
wrong - there is no attribute to break out of. That is why this lint only
inspects event-handler attributes, and why the chart data elsewhere is left
alone.
"""
import pathlib
import re

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / 'gigledger' / 'templates'

# An inline event-handler attribute: onclick="...", onsubmit="...", etc.
HANDLER = re.compile(r'\bon[a-z]+\s*=\s*(?P<q>["\'])(?P<body>.*?)(?P=q)', re.I | re.S)
INTERPOLATION = re.compile(r'\{\{(?P<expr>.*?)\}\}', re.S)


def handler_interpolations():
    """Yield (path, line, expression, handler) for every {{ }} inside an on* attribute."""
    for path in sorted(TEMPLATES.rglob('*.html')):
        text = path.read_text()
        for handler in HANDLER.finditer(text):
            body = handler.group('body')
            for interp in INTERPOLATION.finditer(body):
                line = text[:handler.start('body') + interp.start()].count('\n') + 1
                yield path, line, interp.group('expr').strip(), handler.group(0)


def test_templates_exist():
    """Fail loudly rather than passing vacuously if the path is ever wrong."""
    assert TEMPLATES.is_dir()
    assert len(list(TEMPLATES.rglob('*.html'))) > 10


def test_every_event_handler_interpolation_is_tojson_filtered():
    unsafe = []
    for path, line, expr, _handler in handler_interpolations():
        if not (re.search(r'\|\s*tojson\b', expr)
                and re.search(r'\|\s*forceescape\b', expr)):
            rel = path.relative_to(TEMPLATES.parent.parent)
            unsafe.append(f'{rel}:{line}: {{{{ {expr} }}}}')
    assert not unsafe, (
        'Event-handler interpolations must use | tojson | forceescape (see the '
        'module docstring for why neither filter alone is sufficient):\n'
        + '\n'.join(unsafe)
    )


def test_the_lint_actually_matches_handlers():
    """Guards the test above against passing because the regex matches nothing."""
    found = list(handler_interpolations())
    assert len(found) >= 8, (
        f'Only {len(found)} handler interpolations found; the lint is probably '
        f'not matching real markup, so the test above proves nothing.'
    )


@pytest.mark.parametrize('payload', [
    "x'); alert(1); //",
    'x"); alert(1); //',
    'He said "hi"',
    '</script><script>alert(1)</script>',
    "\\'; alert(1); //",
    'both \' and " quotes',
])
def test_tojson_forceescape_survives_the_html_attribute_round_trip(payload):
    """The property the lint relies on, checked against Flask's own Jinja
    environment rather than a bare one - Flask installs its own tojson."""
    import html as html_module

    from gigledger.app import create_app

    app = create_app()
    rendered = app.jinja_env.from_string(
        '<button onclick="fn({{ v|tojson|forceescape }})">'
    ).render(v=payload)

    match = re.search(r'onclick="([^"]*)"', rendered)
    assert match, f'the attribute was terminated early by the payload: {rendered}'

    # What the JS engine receives, after the HTML parser decodes entities.
    js_source = html_module.unescape(match.group(1))
    assert js_source.startswith('fn("') and js_source.endswith('")')

    body = js_source[len('fn("'):-len('")')]
    assert '"' not in body.replace('\\"', ''), 'unescaped quote closes the literal early'
    assert '<' not in body and '>' not in body, 'raw angle bracket can break out of a tag'
