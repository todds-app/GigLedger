"""Each dashboard section is a framed panel, so one reads apart from the next.

The four headline figures and the four cash figures were loose rows of
cards; each row is now one panel with its own heading, and every other
section is a panel of its own.
"""
import re

from conftest import login_as


def _panels(body):
    return re.findall(r'<div[^>]*class="[^"]*\bdash-panel\b', body)


def test_every_section_is_a_panel(app):
    body = login_as(app).get('/').get_data(as_text=True)
    # Hours, This Month, chart, Quick Add, Cash Position, projects,
    # invoices, recent, expense breakdown.
    assert len(_panels(body)) == 9
    assert 'glass-card' not in body.split('<main', 1)[-1]
    assert 'bouncy-card' not in body


def test_figure_rows_are_grouped_under_headings(app):
    body = login_as(app).get('/').get_data(as_text=True)
    for pid, heading, end, figures in (
            ('money-this-month', 'This Month', '<!-- Chart + Quick Add -->', ('Income', 'Expenses', 'Safe to Spend', 'Tax Reserve')),
            ('cash-position', 'Cash Position', '<!-- Projects + Invoices Row -->', ('Bank Balance', 'Runway', 'Net This Month', 'Monthly Commitments'))):
        start = body.index(f'id="{pid}"')
        assert 'dash-panel' in body[body.rindex('<div', 0, start):start + 60]
        section = body[start:body.index(end, start)]
        assert f'>{heading}</h2>' in section
        for figure in figures:
            assert figure in section


def test_panel_order_keeps_hours_first(app):
    body = login_as(app).get('/').get_data(as_text=True)
    assert (body.index('id="hours-worked"') < body.index('id="money-this-month"')
            < body.index('id="cash-position"'))


def test_panel_style_is_defined():
    css = open('design_portal/static/app.css').read()
    assert re.search(r'\.dash-panel\s*\{[^}]*border: var\(--rule-weight\) solid var\(--hairline\)', css)
