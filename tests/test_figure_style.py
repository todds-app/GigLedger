"""Every figure on the Dashboard and the Transactions totals is drawn the same
way: a tracked-caps label, the value in ink, then a short note. The system has
no status colours, so a value's colour must not vary from figure to figure."""
import re

from conftest import login_as


def _section(body, start, end):
    a = body.index(start)
    return body[a:body.index(end, a)]


def _figures(html):
    return re.findall(
        r'<div class="py-2 border-t border-gray-200">\s*'
        r'<p class="ds-label">([^<]+)</p>\s*'
        r'<p class="(text-[23]xl font-medium text-gray-900 mt-2 tabular-nums)">',
        html)


def test_this_month_figures_share_the_figure_style(app):
    body = login_as(app).get('/').get_data(as_text=True)
    panel = _section(body, 'id="money-this-month"', '<!-- Chart + Quick Add -->')
    assert [label for label, _ in _figures(panel)] == [
        'Income', 'Expenses', 'Safe to Spend', 'Tax Reserve']


def test_this_month_does_not_repeat_its_heading_under_each_figure(app):
    body = login_as(app).get('/').get_data(as_text=True)
    panel = _section(body, 'id="money-this-month"', '<!-- Chart + Quick Add -->')
    assert panel.count('This Month') == 1
    assert 'This month' not in panel


def test_cash_position_figures_share_the_figure_style(app):
    body = login_as(app).get('/').get_data(as_text=True)
    panel = _section(body, 'id="cash-position"', '<!-- Projects + Invoices Row -->')
    assert [label for label, _ in _figures(panel)] == [
        'Bank Balance', 'Runway', 'Net This Month', 'Monthly Commitments']


def test_the_chart_grows_to_fill_its_panel(app):
    body = login_as(app).get('/').get_data(as_text=True)
    assert 'id="income-chart" class="lg:col-span-2 dash-panel p-6 fade-up flex flex-col"' in body
    assert 'class="relative flex-1" style="min-height: 300px;"' in body


def test_transaction_totals_are_figures_in_a_panel(app):
    body = login_as(app).get('/transactions').get_data(as_text=True)
    panel = _section(body, 'id="tx-totals"', '</table>')
    assert 'class="dash-panel p-6 fade-up"' in body[body.index('id="tx-totals"') - 5:
                                                       body.index('id="tx-totals"') + 60]
    assert [label for label, _ in _figures(panel)] == [
        'Total Income', 'Total Expenses', 'Deductible', 'Tax Saving', 'Net']
    assert 'glass-card px-4 py-3 text-center' not in body


def test_deductible_yes_and_no_share_a_case(app):
    body = login_as(app).get('/transactions').get_data(as_text=True)
    assert '<span class="text-sm font-medium text-gray-900">Yes</span>' in body
    assert '<span class="text-sm text-gray-500">No</span>' in body
    assert not re.search(r'<span class="ds-label[^"]*">\s*Yes', body)
