"""The asset pool: what was bought as inventory and where it was bought for.

Read-only. Every write goes through transactions.py, so money has one
authorisation path and this page cannot drift from the ledger. Movements,
quantity remaining and cost recognition are piece 3.
"""
from flask import Blueprint, render_template, request
from flask_login import login_required
from ..models import InventoryItem, Transaction, Project, Business

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
    return items


@inventory_bp.route('/')
@login_required
def index():
    business = Business.get()
    everything = (InventoryItem.query
                  .join(InventoryItem.transaction)
                  .order_by(Transaction.date.desc()).all())

    # The cards describe the whole pool; the filters narrow only the table.
    asset_value = sum(i.total_cost for i in everything)
    on_hand = sum(i.total_cost for i in everything if i.project_id is None)

    return render_template('inventory/index.html',
        items=_filtered(everything, request.args),
        asset_value=asset_value, on_hand=on_hand,
        on_projects=asset_value - on_hand,
        projects=Project.query.order_by(Project.name).all(),
        categories=sorted(business.get_inventory_categories()),
        selected_project=request.args.get('project', ''),
        selected_category=request.args.get('category', ''),
        currency=business.currency)
