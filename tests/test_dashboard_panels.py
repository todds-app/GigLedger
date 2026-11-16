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


# Taxes and Reports take the dashboard's panels: every section framed, and
# each loose row of figure cards gathered into one panel under a heading.

def _section(body, pid, end):
    start = body.index(f'id="{pid}"')
    assert 'dash-panel' in body[body.rindex('<div', 0, start):start + 60]
    return body[start:body.index(end, start)]


def test_taxes_sections_are_panels(app):
    body = login_as(app).get('/taxes').get_data(as_text=True)
    main = body.split('<main', 1)[-1]
    assert len(_panels(body)) == 3
    assert 'glass-card' not in main and 'bouncy-card' not in body
    summary = _section(body, 'tax-summary', '<!-- Quarterly Cards -->')
    assert '>This Year</h2>' in summary
    assert 'Total Tax Obligation' in summary and 'Safe to Spend' in summary
    quarters = _section(body, 'tax-quarters', "<!-- How It's Calculated -->")
    assert '>Quarters</h2>' in quarters
    for q in ('Q1', 'Q2', 'Q3', 'Q4'):
        assert f'>{q}</h3>' in quarters
    # The current quarter is still marked, now by a heavier rule.
    assert quarters.count('border-t-2 border-gray-900') == 1
    assert 'How taxes are calculated' in _section(body, 'tax-method', '</main>')


def test_reports_sections_are_panels(app):
    body = login_as(app).get('/reports').get_data(as_text=True)
    main = body.split('<main', 1)[-1]
    # Year at a Glance, chart, monthly table, top clients, expense
    # categories, quarterly comparison, key insights.
    assert len(_panels(body)) == 7
    assert 'glass-card' not in main and 'bouncy-card' not in body
    year = _section(body, 'year-at-a-glance', '<!-- Income vs Expenses Bar Chart -->')
    assert '>Year at a Glance</h2>' in year
    for figure in ('Income', 'Expenses', 'Net', 'Savings'):
        assert figure in year
    for heading in ('Income vs. Expenses', 'Monthly Breakdown', 'Top Clients',
                    'Expense Categories', 'Quarterly Comparison', 'Key Insights'):
        assert heading in main


# Settings takes the same panels: each section framed, the Danger Zone
# edged so it is not mistaken for an ordinary section.

def test_settings_sections_are_panels(app):
    body = login_as(app).get('/settings').get_data(as_text=True)
    # Business, Tax Rate, Currency, three category lists, Reset Categories,
    # Team, My account, Danger Zone.
    assert len(_panels(body)) == 10
    assert not re.search(r'<div class="glass-card p-6', body)
    for heading in ('Business', 'Tax Rate', 'Currency', 'Income Categories',
                    'Expense Categories', 'Inventory Categories', 'Reset Categories',
                    'Team', 'My account', 'Danger Zone'):
        assert f'>{heading}</h2>' in body
    danger = body.index('>Danger Zone</h2>')
    assert 'dash-panel dash-panel-danger' in body[body.rindex('<div', 0, danger):danger]


def test_danger_panel_style_is_defined():
    css = open('design_portal/static/app.css').read()
    assert re.search(r'\.dash-panel-danger\s*\{[^}]*border-color', css)
