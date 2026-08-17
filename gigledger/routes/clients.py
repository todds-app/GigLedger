from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   abort, session)
from flask_login import login_required, current_user
from .. import portal_auth
from ..models import Client, Invoice, Project, db

clients_bp = Blueprint('clients', __name__, url_prefix='/clients')


@clients_bp.route('/')
@login_required
def list_clients():
    uid = current_user.id
    search = request.args.get('search', '')

    clients = Client.query.filter_by(user_id=uid).order_by(Client.name.asc()).all()

    if search:
        clients = [c for c in clients if search.lower() in c.name.lower()
                   or search.lower() in (c.company or '').lower()
                   or search.lower() in (c.email or '').lower()]

    return render_template('clients/index.html',
        clients=clients, search=search,
        currency=current_user.currency)


@clients_bp.route('/add', methods=['POST'])
@login_required
def add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Client name is required.', 'error')
        return redirect(request.referrer or url_for('clients.list_clients'))

    client = Client(
        user_id=current_user.id,
        name=name,
        email=request.form.get('email', '').strip(),
        phone=request.form.get('phone', '').strip(),
        company=request.form.get('company', '').strip(),
        address=request.form.get('address', '').strip(),
        notes=request.form.get('notes', '').strip(),
        is_active=True)
    db.session.add(client)
    db.session.commit()

    flash(f'Client "{name}" added!', 'success')
    return redirect(url_for('clients.list_clients'))


@clients_bp.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit(id):
    client = Client.query.filter_by(id=id, user_id=current_user.id).first()
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('clients.list_clients'))

    name = request.form.get('name', '').strip()
    if not name:
        flash('Client name is required.', 'error')
        return redirect(request.referrer or url_for('clients.detail', id=id))

    client.name = name
    client.email = request.form.get('email', '').strip()
    client.phone = request.form.get('phone', '').strip()
    client.company = request.form.get('company', '').strip()
    client.address = request.form.get('address', '').strip()
    client.notes = request.form.get('notes', '').strip()
    client.is_active = request.form.get('is_active') == 'on'
    db.session.commit()

    flash(f'Client "{name}" updated!', 'success')
    return redirect(url_for('clients.detail', id=id))


@clients_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    client = Client.query.filter_by(id=id, user_id=current_user.id).first()
    if client:
        name = client.name
        db.session.delete(client)
        db.session.commit()
        flash(f'Client "{name}" deleted.', 'success')
    else:
        flash('Client not found.', 'error')
    return redirect(url_for('clients.list_clients'))


@clients_bp.route('/<int:id>')
@login_required
def detail(id):
    client = Client.query.filter_by(id=id, user_id=current_user.id).first()
    if not client:
        flash('Client not found.', 'error')
        return redirect(url_for('clients.list_clients'))

    invoices = Invoice.query.filter_by(client_id=id).order_by(Invoice.issue_date.desc()).all()
    projects = Project.query.filter_by(client_id=id).order_by(Project.created_at.desc()).all()

    total_invoiced = client.total_invoiced()
    total_paid = client.total_paid()
    total_outstanding = client.total_outstanding()

    return render_template('clients/detail.html',
        client=client,
        invoices=invoices,
        projects=projects,
        total_invoiced=total_invoiced,
        total_paid=total_paid,
        total_outstanding=total_outstanding,
        portal_enabled=portal_auth.is_enabled(),
        invite_url=session.pop('portal_invite_url', None),
        currency=current_user.currency)


# --- Client Portal access ------------------------------------------------
#
# The freelancer delivers the invite themselves. GigLedger sends no email, and
# an invite flow that quietly depends on working SMTP is a flow that quietly
# fails. See docs/adr/0008.

def _owned_client(id):
    client = Client.query.filter_by(id=id, user_id=current_user.id).first()
    if not client:
        abort(404)
    return client


@clients_bp.route('/<int:id>/portal/invite', methods=['POST'])
@login_required
def portal_invite(id):
    client = _owned_client(id)

    email = (request.form.get('email', '') or client.email or '').strip().lower()
    if not email:
        flash('An email address is needed to create a portal invitation.', 'error')
        return redirect(url_for('clients.detail', id=client.id))

    _, token = portal_auth.create_invite(client, email)
    db.session.commit()

    # Handed back through the session for one render. The token exists nowhere
    # else in a readable form, so this page is the only chance to copy it - and
    # putting it in the session rather than the URL keeps it out of the browser
    # history and out of any access log.
    session['portal_invite_url'] = url_for('portal.redeem', token=token,
                                           _external=True)
    flash('Invitation created. Copy the link below and send it to your client.',
          'success')
    return redirect(url_for('clients.detail', id=client.id))


@clients_bp.route('/<int:id>/portal/revoke', methods=['POST'])
@login_required
def portal_revoke(id):
    client = _owned_client(id)

    portal_auth.revoke(client)
    db.session.commit()

    flash(f'Portal access for {client.name} has been revoked.', 'success')
    return redirect(url_for('clients.detail', id=client.id))
