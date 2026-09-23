"""The asset pool: what was bought as inventory and where it was bought for.

Read-only. Every write goes through transactions.py, so money has one
authorisation path and this page cannot drift from the ledger. Movements,
quantity remaining and consumption are piece 3; a sale is the invoice line
that bills the purchase (docs/adr/0017) and the Sell button posts there.
"""
from flask import Blueprint, render_template, request
from flask_login import login_required
from ..models import InventoryItem, Transaction, Project, Business, Client, Invoice

inventory_bp = Blueprint('inventory', __name__, url_prefix='/inventory')


def _filtered(items, args):
    project = args.get('project', '')
    if project == 'general':
        items = [i for i in items if i.project_id is None]
    elif project:
        try:
            pid = int(project)
            items = [i for i in items if i.project_id == pid]
        except ValueError:
            pass

    category = args.get('category', '')
    if category:
        items = [i for i in items if i.transaction.category == category]

    status = args.get('status', '')
    if status in ('sold', 'unsold'):
        items = [i for i in items if i.is_sold == (status == 'sold')]
    return items


@inventory_bp.route('/')
@login_required
def index():
    business = Business.get()
    everything = (InventoryItem.query
                  .join(InventoryItem.transaction)
                  .order_by(Transaction.date.desc()).all())

    # The cards describe the whole pool; the filters narrow only the table.
    # A sold item has left the pool whatever its invoice's status: the sale
    # is agreed, and the money side is the invoice's to settle.
    owned = [i for i in everything if not i.is_sold]
    sold = [i for i in everything if i.is_sold]
    asset_value = sum(i.total_cost for i in owned)
    on_hand = sum(i.total_cost for i in owned if i.project_id is None)

    return render_template('inventory/index.html',
        items=_filtered(everything, request.args),
        asset_value=asset_value, on_hand=on_hand,
        on_projects=asset_value - on_hand,
        sold_total=sum(i.sale_price for i in sold),
        sold_profit=sum(i.profit for i in sold),
        projects=Project.query.order_by(Project.name).all(),
        categories=sorted(business.get_inventory_categories()),
        selected_project=request.args.get('project', ''),
        selected_category=request.args.get('category', ''),
        selected_status=request.args.get('status', ''),
        draft_invoices=Invoice.query.filter_by(status='draft').order_by(Invoice.created_at.desc()).all(),
        clients=Client.query.filter_by(is_active=True).order_by(Client.name).all(),
        currency=business.currency)
