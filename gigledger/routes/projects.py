"""
GigLedger - Projects Blueprint
"""
import os
import re
from datetime import datetime
from flask import (Blueprint, render_template, redirect, url_for, request, flash,
                   abort, send_file)
from flask_login import login_required, current_user
from .. import documents
from ..models import (Project, Client, ProjectDocument, DocumentShare,
                      Transaction, db, INCOME)

projects_bp = Blueprint('projects', __name__, url_prefix='/projects')

# These two columns only ever hold a value from a fixed set, so enforce that at
# the write rather than trusting the form. The previous colour check accepted
# anything beginning with '#', which let arbitrary text into a field that is
# interpolated into templates.
RATE_TYPES = {'hourly', 'fixed', 'daily'}
DEFAULT_RATE_TYPE = 'hourly'
DEFAULT_COLOR = '#34d399'
HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


def clean_rate_type(value, fallback=DEFAULT_RATE_TYPE):
    return value if value in RATE_TYPES else fallback


def clean_color(value, fallback=DEFAULT_COLOR):
    return value if value and HEX_COLOR.match(value) else fallback


@projects_bp.route('/')
@login_required
def list_projects():
    uid = current_user.id
    status_filter = request.args.get('status', 'all')

    all_projects = Project.query.filter_by(user_id=uid).order_by(Project.created_at.desc()).all()

    if status_filter and status_filter != 'all':
        projects = [p for p in all_projects if p.status == status_filter]
    else:
        projects = all_projects

    # Summary stats
    active_count = len([p for p in all_projects if p.status == 'active'])
    total_earned = sum(p.earned for p in all_projects)

    now = datetime.now()
    hours_this_month = sum(
        p.hours_logged for p in all_projects
        if p.start_date and p.start_date.month == now.month and p.start_date.year == now.year
    )

    clients = Client.query.filter_by(user_id=uid, is_active=True).order_by(Client.name).all()

    return render_template('projects/index.html',
        projects=projects,
        all_projects=all_projects,
        clients=clients,
        document_stats=documents.per_project_stats(uid),
        recent_cutoff=documents.recent_cutoff(),
        active_count=active_count,
        total_earned=total_earned,
        hours_this_month=hours_this_month,
        status_filter=status_filter,
        now=datetime.now(),
        currency=current_user.currency)


@projects_bp.route('/add', methods=['POST'])
@login_required
def add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Project name is required.', 'error')
        return redirect(url_for('projects.list_projects'))

    # Only accept a client id that belongs to the current user (prevents IDOR).
    client_id_raw = request.form.get('client_id', '')
    client_id = None
    if client_id_raw and client_id_raw.isdigit():
        owned = Client.query.filter_by(id=int(client_id_raw), user_id=current_user.id).first()
        client_id = owned.id if owned else None

    rate_type = clean_rate_type(request.form.get('rate_type', DEFAULT_RATE_TYPE))
    try:
        rate = float(request.form.get('rate', '0'))
    except ValueError:
        rate = 0

    start_date_str = request.form.get('start_date', '')
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        start_date = datetime.now()

    deadline_str = request.form.get('deadline', '')
    try:
        deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        deadline = None

    color = clean_color(request.form.get('color', DEFAULT_COLOR))

    project = Project(
        user_id=current_user.id,
        client_id=client_id,
        name=name,
        description=request.form.get('description', ''),
        status='active',
        rate_type=rate_type,
        rate=rate,
        start_date=start_date,
        deadline=deadline,
        color=color)
    db.session.add(project)
    db.session.commit()

    flash(f'Project "{name}" created!', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit(id):
    project = Project.query.filter_by(id=id, user_id=current_user.id).first()
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('projects.list_projects'))

    name = request.form.get('name', '').strip()
    if not name:
        flash('Project name is required.', 'error')
        return redirect(url_for('projects.list_projects'))

    # Only accept a client id that belongs to the current user (prevents IDOR).
    client_id_raw = request.form.get('client_id', '')
    if client_id_raw and client_id_raw.isdigit():
        owned = Client.query.filter_by(id=int(client_id_raw), user_id=current_user.id).first()
        project.client_id = owned.id if owned else None
    else:
        project.client_id = None
    project.name = name
    project.description = request.form.get('description', '')
    project.rate_type = clean_rate_type(request.form.get('rate_type', DEFAULT_RATE_TYPE),
                                        fallback=project.rate_type)
    try:
        project.rate = float(request.form.get('rate', '0'))
    except ValueError:
        pass

    start_date_str = request.form.get('start_date', '')
    try:
        project.start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        pass

    deadline_str = request.form.get('deadline', '')
    try:
        project.deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
    except (ValueError, TypeError):
        project.deadline = None

    project.color = clean_color(request.form.get('color', project.color),
                                fallback=project.color)

    db.session.commit()
    flash(f'Project "{name}" updated!', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/log-hours/<int:id>', methods=['POST'])
@login_required
def log_hours(id):
    project = Project.query.filter_by(id=id, user_id=current_user.id).first()
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('projects.list_projects'))

    try:
        hours = float(request.form.get('hours', '0'))
    except ValueError:
        flash('Invalid hours value.', 'error')
        return redirect(url_for('projects.list_projects'))

    if hours <= 0:
        flash('Hours must be greater than zero.', 'error')
        return redirect(url_for('projects.list_projects'))

    project.hours_logged = (project.hours_logged or 0) + hours

    # Optionally create a transaction for the earned amount
    create_transaction = request.form.get('create_transaction') == 'on'
    if create_transaction and project.earned > 0:
        earned = project.earned
        tx = Transaction(
            user_id=current_user.id,
            amount=earned,
            date=datetime.now(),
            kind=INCOME,
            category='Freelance Project',
            description=f'Hours logged on project: {project.name}',
            is_tax_deductible=False,
            source='project')
        db.session.add(tx)

    db.session.commit()
    flash(f'{hours}h logged on "{project.name}"!', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/update-status/<int:id>', methods=['POST'])
@login_required
def update_status(id):
    project = Project.query.filter_by(id=id, user_id=current_user.id).first()
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('projects.list_projects'))

    new_status = request.form.get('status', 'active')
    valid_statuses = ['active', 'completed', 'on_hold', 'cancelled']
    if new_status not in valid_statuses:
        flash('Invalid status.', 'error')
        return redirect(url_for('projects.list_projects'))

    project.status = new_status
    if new_status == 'completed':
        project.end_date = datetime.now()

    db.session.commit()
    flash(f'Project "{project.name}" marked as {new_status.replace("_", " ").title()}.', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    project = Project.query.filter_by(id=id, user_id=current_user.id).first()
    if project:
        # The ORM cascade removes the document rows; nothing in SQLAlchemy
        # removes their bytes, and SQLite is not enforcing the foreign key
        # either (ADR-0006). Collect the names before the rows go, unlink after
        # the commit succeeds - so a failed delete never orphans a live row from
        # its file.
        stored = [d.stored_name for d in project.documents if d.stored_name]
        db.session.delete(project)
        db.session.commit()
        for stored_name in stored:
            documents.delete(stored_name)
        flash('Project deleted.', 'success')
    else:
        flash('Project not found.', 'error')
    return redirect(url_for('projects.list_projects'))


# --- Documents -----------------------------------------------------------
#
# Ownership is checked by filtering on user_id at every entry point rather than
# by fetching and then comparing, so a missed comparison cannot expose a row.
# In this commit "may see it" means "owns it"; the client-facing half of the
# rule arrives with the portal.

def _owned_project(id):
    project = Project.query.filter_by(id=id, user_id=current_user.id).first()
    if not project:
        abort(404)
    return project


def _owned_document(doc_id):
    doc = ProjectDocument.query.filter_by(id=doc_id, user_id=current_user.id).first()
    if not doc:
        abort(404)
    return doc


@projects_bp.route('/<int:id>')
@login_required
def detail(id):
    project = _owned_project(id)
    return render_template('projects/detail.html',
        project=project,
        documents=ProjectDocument.query.filter_by(project_id=project.id)
                                       .order_by(ProjectDocument.created_at.desc()).all(),
        clients=Client.query.filter_by(user_id=current_user.id, is_active=True)
                            .order_by(Client.name).all(),
        max_upload_mb=documents.MAX_UPLOAD_BYTES // (1024 * 1024),
        now=datetime.now(),
        currency=current_user.currency)


@projects_bp.route('/<int:id>/documents/upload', methods=['POST'])
@login_required
def upload_document(id):
    project = _owned_project(id)

    upload = request.files.get('file')
    if not upload or not upload.filename:
        flash('Choose a file to upload.', 'error')
        return redirect(url_for('projects.detail', id=project.id))

    if not documents.is_allowed_upload(upload.filename):
        flash(f'That file type is not accepted. Allowed: '
              f'{", ".join(sorted(e.lstrip(".") for e in documents.ALLOWED_EXTENSIONS))}.',
              'error')
        return redirect(url_for('projects.detail', id=project.id))

    stored_name, byte_size = documents.store(upload)

    db.session.add(ProjectDocument(
        user_id=current_user.id,
        project_id=project.id,
        kind='upload',
        title=request.form.get('title', '').strip() or upload.filename,
        stored_name=stored_name,
        original_name=upload.filename,
        byte_size=byte_size))
    db.session.commit()

    flash('Document uploaded.', 'success')
    return redirect(url_for('projects.detail', id=project.id))


@projects_bp.route('/<int:id>/documents/link', methods=['POST'])
@login_required
def link_document(id):
    project = _owned_project(id)

    url = documents.clean_external_url(request.form.get('url', ''))
    if not url:
        flash('Enter a document link starting with http:// or https://.', 'error')
        return redirect(url_for('projects.detail', id=project.id))

    db.session.add(ProjectDocument(
        user_id=current_user.id,
        project_id=project.id,
        kind='link',
        title=request.form.get('title', '').strip() or url,
        external_url=url,
        provider=documents.provider_of(url)))
    db.session.commit()

    flash('Document link added.', 'success')
    return redirect(url_for('projects.detail', id=project.id))


@projects_bp.route('/documents/<int:doc_id>/download')
@login_required
def download_document(doc_id):
    doc = _owned_document(doc_id)
    if doc.kind != 'upload':
        abort(404)

    path = documents.path_for(doc.stored_name)
    if not os.path.exists(path):
        # The row outlived its bytes. A 404 is the honest answer; a 500 would
        # send the reader looking for a bug in the download path.
        abort(404)

    documents.record_access(doc, user_id=current_user.id)

    # Always an attachment, always a type no browser will try to render. The
    # file's real type is not consulted: this response must not become a page
    # on this origin no matter what was uploaded.
    return send_file(path, mimetype='application/octet-stream',
                     as_attachment=True,
                     download_name=doc.original_name or 'document')


@projects_bp.route('/documents/<int:doc_id>/share', methods=['POST'])
@login_required
def share_document(doc_id):
    """Set the whole grant list for a document, rather than adding one at a time.

    The form posts the complete set of clients who should have it, so a client
    absent from the post is a client whose access is withdrawn - unchecking a box
    revokes, without a separate unshare route that could be forgotten.
    """
    doc = _owned_document(doc_id)

    # Only ids that are this user's clients. Filtering rather than validating
    # means an id belonging to somebody else is dropped, not honoured.
    requested = {int(v) for v in request.form.getlist('client_ids') if v.isdigit()}
    allowed = {c.id for c in Client.query.filter_by(user_id=current_user.id).all()}
    target = requested & allowed

    current = doc.shared_client_ids
    for client_id in target - current:
        db.session.add(DocumentShare(document_id=doc.id, client_id=client_id))
    for share in list(doc.shares):
        if share.client_id not in target:
            db.session.delete(share)
    db.session.commit()

    flash('Sharing updated.' if target else 'Document is no longer shared.', 'success')
    return redirect(url_for('projects.detail', id=doc.project_id))


@projects_bp.route('/documents/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete_document(doc_id):
    doc = _owned_document(doc_id)
    project_id, stored_name = doc.project_id, doc.stored_name

    db.session.delete(doc)
    db.session.commit()
    documents.delete(stored_name)

    flash('Document removed.', 'success')
    return redirect(url_for('projects.detail', id=project_id))
