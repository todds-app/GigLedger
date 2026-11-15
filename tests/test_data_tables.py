"""The four list tables share one look: a framed panel, quiet stripes, a
sticky header ruled in ink, and each row's actions folded into one menu.

The demo seed has invoices, transactions and recurring rows; an inventory
purchase is added so every page has at least one menu.
"""
import re
from datetime import datetime

import pytest

from design_portal.models import INVENTORY, InventoryItem, Transaction, db
from conftest import login_as


@pytest.fixture
def stocked(app):
    with app.app_context():
        tx = Transaction(user_id=1, amount=-100, date=datetime(2026, 9, 10), kind=INVENTORY,
                         category='Seating', description='Folding chairs', source='manual')
        tx.inventory_item = InventoryItem(user_id=1, quantity=4, unit_cost=25)
        db.session.add(tx)
        db.session.commit()
    return app

PAGES = ['/invoices', '/transactions', '/recurring/', '/inventory/']


@pytest.mark.parametrize('url', PAGES)
def test_table_is_framed_and_striped(stocked, url):
    body = login_as(stocked).get(url).get_data(as_text=True)
    assert 'class="data-table-panel' in body
    assert re.search(r'<table class="[^"]*\bdata-table\b', body)
    # The old translucent header is gone; the sticky header lives in app.css.
    assert 'bg-white/80' not in body


@pytest.mark.parametrize('url', PAGES)
def test_row_actions_are_one_menu(stocked, url):
    body = login_as(stocked).get(url).get_data(as_text=True)
    menus = body.count('<details class="row-menu">')
    assert menus >= 1
    # Every menu button names its row for screen readers.
    assert len(re.findall(r'<summary aria-label="Actions for [^"]+"', body)) == menus
    assert '<span class="sr-only">Actions</span>' in body
    # No loose action words left in the actions cell.
    assert 'class="ds-label hover:text-gray-900" title="Delete"' not in body


def test_menus_keep_their_actions(app):
    client = login_as(app)
    invoices = client.get('/invoices').get_data(as_text=True)
    assert '>View</a>' in invoices and '>Delete</button>' in invoices
    tx = client.get('/transactions').get_data(as_text=True)
    assert 'class="row-menu-item">Edit</button>' in tx
    rec = client.get('/recurring/').get_data(as_text=True)
    assert 'class="row-menu-item">Edit</button>' in rec and 'class="row-menu-item">Delete</button>' in rec


def test_inventory_menu_offers_sell_and_edit(stocked):
    body = login_as(stocked).get('/inventory/').get_data(as_text=True)
    assert 'aria-label="Actions for Folding chairs"' in body
    assert 'class="row-menu-item">Sell</button>' in body
    assert 'class="row-menu-item">Edit</button>' in body


def test_menu_script_is_served(app):
    client = login_as(app)
    assert 'static/row-menu.js' in client.get('/transactions').get_data(as_text=True)
    js = client.get('/static/row-menu.js')
    assert js.status_code == 200 and b"addEventListener('toggle'" in js.data


def test_table_styles_are_defined():
    css = open('design_portal/static/app.css').read()
    assert '.data-table tbody tr:nth-child(even)' in css
    assert re.search(r'\.data-table thead th \{[^}]*position: sticky', css)
    assert re.search(r'\.row-menu-list \{[^}]*position: fixed', css)
