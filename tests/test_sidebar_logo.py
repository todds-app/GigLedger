"""The logo badge heads the sidebar, above the wordmark, and is not repeated
beside the account details at the bottom."""
from conftest import login_as


def _sidebar(body):
    return body[body.index('<aside'):body.index('</aside>')]


def test_logo_sits_once_at_the_top_of_the_sidebar(app):
    side = _sidebar(login_as(app).get('/').get_data(as_text=True))
    assert side.count('dsd/logo-badge.png') == 1
    logo = side.index('dsd/logo-badge.png')
    assert logo < side.index('Dani Smith Design</p>') < side.index('<nav')


def test_bottom_of_the_sidebar_keeps_the_account_without_the_logo(app):
    side = _sidebar(login_as(app).get('/').get_data(as_text=True))
    bottom = side[side.index('</nav>'):]
    assert 'logo-badge' not in bottom
    assert '% tax rate' in bottom and 'Sign out' in bottom


def test_logo_leads_to_the_dashboard(app):
    side = _sidebar(login_as(app).get('/settings').get_data(as_text=True))
    link = side[side.rindex('<a ', 0, side.index('logo-badge')):side.index('logo-badge')]
    assert 'class="sidebar-mark"' in link and 'href="/"' in link
    assert 'aria-label="Dani Smith Design, go to Dashboard"' in link
