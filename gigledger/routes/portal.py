"""
GigLedger - Client Portal Blueprint.

Registered only when PORTAL_ENABLED is on, so a deployment that does not want an
internet-facing client login does not merely refuse those routes - it does not
have them. See docs/adr/0008.

Every view here is @client_required except login and invite redemption, which
cannot be: you cannot log in from inside a session you do not have.
tests/test_portal_auth.py walks the url_map and fails on any other exception.
"""
import os

from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   abort, g, send_file)

from .. import documents as documents_module
from .. import portal_auth
from ..portal_auth import client_required
from ..models import PortalAccount, Project, ProjectDocument, User, db

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

SCOPE = 'portal'


@portal_bp.route('/login', methods=['GET', 'POST'])
def login():
    if portal_auth.current_account():
        return redirect(url_for('portal.index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        # Checked before the password is, so a locked-out identifier costs an
        # attacker a lookup rather than a hash comparison.
        if portal_auth.is_locked_out(SCOPE, email):
            flash('Too many failed attempts. Try again in a few minutes.', 'error')
            return render_template('portal/login.html')

        account = PortalAccount.query.filter_by(email=email).first()
        if portal_auth.check_password(account, password):
            portal_auth.clear_failures(SCOPE, email)
            portal_auth.log_in(account)
            return redirect(url_for('portal.index'))

        portal_auth.record_failure(SCOPE, email)
        flash('Invalid email or password.', 'error')

    return render_template('portal/login.html')


@portal_bp.route('/logout', methods=['POST'])
@client_required
def logout():
    portal_auth.log_out()
    flash('You have been signed out.', 'info')
    return redirect(url_for('portal.login'))


@portal_bp.route('/invite/<token>', methods=['GET', 'POST'])
def redeem(token):
    invite = portal_auth.open_invite(token)
    if not invite:
        # One message for expired, already-used and never-existed alike: the
        # difference between them is not the visitor's business.
        flash('That invitation link is no longer valid. Ask for a new one.', 'error')
        return redirect(url_for('portal.login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if password != request.form.get('confirm_password', ''):
            flash('Passwords do not match.', 'error')
            return render_template('portal/redeem.html', invite=invite, token=token)

        account, error = portal_auth.redeem_invite(invite, password)
        if error:
            db.session.rollback()
            flash(error, 'error')
            return render_template('portal/redeem.html', invite=invite, token=token)

        db.session.commit()
        portal_auth.log_in(account)
        flash('Your portal access is set up.', 'success')
        return redirect(url_for('portal.index'))

    return render_template('portal/redeem.html', invite=invite, token=token)


@portal_bp.route('/')
@client_required
def index():
    clients = portal_auth.visible_clients(g.portal_account)
    documents = documents_module.documents_shared_with(clients)

    # "New" means shared since this account last loaded this page. Computed
    # before the stamp moves, or nothing would ever be new. See ADR-0012.
    new_ids = documents_module.newly_shared_ids(clients, g.portal_account.documents_seen_at)
    documents_module.mark_documents_seen(g.portal_account)

    # Grouped by the freelancer who shared them, then by project. A portal
    # account is global (ADR-0008), so one page can carry two tenants' material;
    # rendering it as one undifferentiated list would be a leak of context even
    # though every individual row is authorised.
    by_owner = {}
    for doc in documents:
        owner = db.session.get(User, doc.user_id)
        group = by_owner.setdefault(doc.user_id, {
            'name': owner.business_name or owner.email,
            'projects': {},
        })
        project = db.session.get(Project, doc.project_id)
        group['projects'].setdefault(project.name, []).append(doc)

    return render_template('portal/index.html',
                           account=g.portal_account,
                           groups=list(by_owner.values()),
                           new_ids=new_ids,
                           new_count=len(new_ids))


@portal_bp.route('/documents/<int:doc_id>/download')
@client_required
def download(doc_id):
    """The client-facing half of the rule the owner's download route enforces.

    Written as its own route rather than as a branch inside the owner's, so the
    two authorisation questions - "do you own it" and "was it granted to you" -
    never share a code path where one could be reached with the other's answer.
    """
    doc = db.session.get(ProjectDocument, doc_id)
    clients = portal_auth.visible_clients(g.portal_account)
    if not doc or doc.kind != 'upload' or not documents_module.is_shared_with(doc, clients):
        # One 404 for "no such document", "not shared with you" and "it is a
        # link". Which of those it is would itself be information.
        abort(404)

    path = documents_module.path_for(doc.stored_name)
    if not os.path.exists(path):
        abort(404)

    documents_module.record_access(doc, portal_account_id=g.portal_account.id)

    return send_file(path, mimetype='application/octet-stream',
                     as_attachment=True,
                     download_name=doc.original_name or 'document')
