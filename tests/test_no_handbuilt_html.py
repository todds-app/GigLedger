"""Route-layer HTML lint.

Guards the bug class fixed in docs/adr/0005: HTML assembled as an f-string
inside a route, which interpolates user data with no escaping. The invoice and
transaction exports were built that way, so a client name or line-item
description went into the document raw.

Templates are the fix, because Jinja autoescapes by default: the next field
someone adds is safe without anyone remembering to escape it. This test keeps
routes from drifting back.
"""
import ast
import pathlib

import pytest

ROUTES = pathlib.Path(__file__).resolve().parent.parent / 'gigledger' / 'routes'

# Substrings that mark a string literal as HTML rather than, say, a flash
# message that happens to contain an angle bracket.
HTML_MARKERS = ('<!DOCTYPE', '<html', '<div', '<td', '<tr', '<span', '<table', '<body')


def route_files():
    return sorted(p for p in ROUTES.glob('*.py') if p.name != '__init__.py')


def looks_like_html(text):
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in HTML_MARKERS)


def test_route_files_are_found():
    """Fail loudly rather than passing vacuously if the path is ever wrong."""
    files = route_files()
    assert len(files) >= 10, f'only found {len(files)} route modules'


@pytest.mark.parametrize('path', route_files(), ids=lambda p: p.name)
def test_no_html_built_by_string_interpolation(path):
    """HTML containing interpolated values must come from a template.

    Catches f-strings (JoinedStr) and `%`/`.format()`/`+` concatenation whose
    result looks like markup.
    """
    tree = ast.parse(path.read_text())
    offenders = []

    for node in ast.walk(tree):
        # f-string containing HTML and at least one interpolated expression
        if isinstance(node, ast.JoinedStr):
            literal = ''.join(
                v.value for v in node.values if isinstance(v, ast.Constant)
                and isinstance(v.value, str)
            )
            has_interpolation = any(
                isinstance(v, ast.FormattedValue) for v in node.values
            )
            if has_interpolation and looks_like_html(literal):
                offenders.append(f'line {node.lineno}: f-string building HTML')

        # "..." % (...) and "...".format(...) and "..." + x, where the literal is HTML
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
            left = node.left
            if (isinstance(left, ast.Constant) and isinstance(left.value, str)
                    and looks_like_html(left.value)):
                offenders.append(f'line {node.lineno}: HTML built by concatenation')
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == 'format'
              and isinstance(node.func.value, ast.Constant)
              and isinstance(node.func.value.value, str)
              and looks_like_html(node.func.value.value)):
            offenders.append(f'line {node.lineno}: HTML built by .format()')

    assert not offenders, (
        f'{path.name} builds HTML by interpolation instead of rendering a '
        f'template, so the interpolated values are unescaped:\n  '
        + '\n  '.join(offenders)
    )


def test_the_lint_recognises_html():
    """Guards the parametrised test against a marker list that matches nothing."""
    assert looks_like_html('<!DOCTYPE html><html><body>hi</body></html>')
    assert looks_like_html('<td style="x">{value}</td>')
    assert not looks_like_html('Category "x" already exists.')
    assert not looks_like_html('Invoice not found.')
