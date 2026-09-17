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
from ..models import (Business, Project, Client, ProjectDocument, DocumentShare,
                      Transaction, InventoryItem, TimeLog, db, INCOME, round_quarter)

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
    status_filter = request.args.get('status', 'all')

    all_projects = Project.query.order_by(Project.created_at.desc()).all()

    if status_filter and status_filter != 'all':
        projects = [p for p in all_projects if p.status == status_filter]
    else:
        projects = all_projects

    # Summary stats
    active_count = len([p for p in all_projects if p.status == 'active'])
    total_earned = sum(p.earned for p in all_projects)

    now = datetime.now()
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hours_this_month = sum(
        log.hours for log in TimeLog.query.filter(TimeLog.ended_on >= first_of_month))

    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()

    return render_template('projects/index.html',
        projects=projects,
        all_projects=all_projects,
        clients=clients,
        document_stats=documents.per_project_stats(),
        recent_cutoff=documents.recent_cutoff(),
        active_count=active_count,
        total_earned=total_earned,
        hours_this_month=hours_this_month,
        status_filter=status_filter,
        now=now,
        currency=Business.get().currency)


@projects_bp.route('/add', methods=['POST'])
@login_required
def add():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Project name is required.', 'error')
        return redirect(url_for('projects.list_projects'))

    # Only accept a client id that actually exists (prevents IDOR).
    client_id_raw = request.form.get('client_id', '')
    client_id = None
    if client_id_raw and client_id_raw.isdigit():
        owned = db.session.get(Client, int(client_id_raw))
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
    project = Project.query.filter_by(id=id).first()
    if not project:
        flash('Project not found.', 'error')
        return redirect(url_for('projects.list_projects'))

    name = request.form.get('name', '').strip()
    if not name:
        flash('Project name is required.', 'error')
        return redirect(url_for('projects.list_projects'))

    # Only accept a client id that actually exists (prevents IDOR).
    client_id_raw = request.form.get('client_id', '')
    if client_id_raw and client_id_raw.isdigit():
        owned = db.session.get(Client, int(client_id_raw))
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


def _parse_day(value, default):
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except (ValueError, TypeError):
        return default


@projects_bp.route('/log-hours/<int:id>', methods=['POST'])
@login_required
def log_hours(id):
    """Bank the timer as a TimeLog. The posted hours are authoritative - the
    modal's prefill is a suggestion - and are rounded by the one rule. Nothing
    is booked to the ledger here; that is a separate, deliberate click on the
    project page (ADR-0015)."""
    project = _owned_project(id)
    now = datetime.now()
    back = redirect(url_for('projects.list_projects'))

    try:
        hours = round_quarter(float(request.form.get('hours', '0')))
    except ValueError:
        flash('Invalid hours value.', 'error')
        return back
    if hours <= 0:
        flash('Hours must be greater than zero.', 'error')
        return back

    ended_on = _parse_day(request.form.get('ended_on'), now)
    started_on = _parse_day(request.form.get('started_on'),
                            project.timer_since or ended_on)
    if started_on.date() > ended_on.date():
        flash('The hours cannot start after they end.', 'error')
        return back

    project.stop_timer(now)
    db.session.add(TimeLog(
        user_id=current_user.id,
        project_id=project.id,
        hours=hours,
        started_on=started_on,
        ended_on=ended_on))
    project.reset_timer()
    db.session.commit()

    flash(f'{hours:g}h logged on "{project.name}".', 'success')
    return back


# --- Timer -----------------------------------------------------------------
#
# One clock for the whole business: a person works on one thing at a time, so
# starting a project's timer stops whichever other one is running and banks
# its seconds there. State lives on the project row (ADR-0015); these routes
# only move it and commit.

@projects_bp.route('/<int:id>/timer/start', methods=['POST'])
@login_required
def timer_start(id):
    project = _owned_project(id)
    now = datetime.now()
    for other in Project.query.filter(Project.timer_started_at.isnot(None),
                                      Project.id != project.id).all():
        other.stop_timer(now)
    project.start_timer(now)
    db.session.commit()
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/<int:id>/timer/stop', methods=['POST'])
@login_required
def timer_stop(id):
    project = _owned_project(id)
    project.stop_timer(datetime.now())
    db.session.commit()
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/update-status/<int:id>', methods=['POST'])
@login_required
def update_status(id):
    project = Project.query.filter_by(id=id).first()
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
    if new_status != 'active':
        # Nothing counts unseen: a project put on hold or finished stops its
        # clock. The seconds are kept for the next Log Hours.
        project.stop_timer(datetime.now())

    db.session.commit()
    flash(f'Project "{project.name}" marked as {new_status.replace("_", " ").title()}.', 'success')
    return redirect(url_for('projects.list_projects'))


@projects_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete(id):
    project = Project.query.filter_by(id=id).first()
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


# --- Time logs -------------------------------------------------------------
#
# A log is editable and deletable until it has been billed. Once a transaction
# hangs off it the hours are what the ledger says they are, so both refuse.

@projects_bp.route('/time-logs/<int:log_id>/edit', methods=['POST'])
@login_required
def edit_time_log(log_id):
    log = _owned_time_log(log_id)
    back = redirect(url_for('projects.detail', id=log.project_id))
    if log.is_billed:
        flash('These hours already have a transaction. Delete it to edit them.', 'error')
        return back

    try:
        hours = round_quarter(float(request.form.get('hours', '0')))
    except ValueError:
        flash('Invalid hours value.', 'error')
        return back
    if hours <= 0:
        flash('Hours must be greater than zero.', 'error')
        return back

    log.hours = hours
    db.session.commit()
    flash('Hours updated.', 'success')
    return back


@projects_bp.route('/time-logs/<int:log_id>/delete', methods=['POST'])
@login_required
def delete_time_log(log_id):
    log = _owned_time_log(log_id)
    back = redirect(url_for('projects.detail', id=log.project_id))
    if log.is_billed:
        flash('These hours already have a transaction. Delete it first.', 'error')
        return back

    db.session.delete(log)
    db.session.commit()
    flash('Hours removed.', 'success')
    return back


@projects_bp.route('/time-logs/<int:log_id>/create-transaction', methods=['POST'])
@login_required
def create_time_log_transaction(log_id):
    """Book a log as income: hours x the project's hourly rate, dated when the
    hours ended. The amount is derived here, never posted (ADR-0011's rule),
    and linking the row is what locks the log."""
    log = _owned_time_log(log_id)
    project = log.project
    back = redirect(url_for('projects.detail', id=project.id))
    if log.is_billed:
        flash('These hours already have a transaction.', 'error')
        return back
    if project.rate_type != 'hourly':
        flash('Only hourly projects bill by the hour.', 'error')
        return back
    if not project.rate or project.rate <= 0:
        flash('Set an hourly rate on the project first.', 'error')
        return back

    log.transaction = Transaction(
        user_id=current_user.id,
        amount=log.hours * project.rate,
        date=log.ended_on,
        kind=INCOME,
        category='Freelance Project',
        description=f'{log.hours:g}h on {project.name} ({log.date_range_label})',
        is_tax_deductible=False,
        source='project')
    db.session.commit()
    flash(f'Income of {log.hours * project.rate:.2f} booked for {log.hours:g}h.', 'success')
    return back


@projects_bp.route('/<int:id>/time-logs/bill-all', methods=['POST'])
@login_required
def bill_all_time_logs(id):
    """Book every unbilled log as one income transaction: the summed hours
    x the rate, dated when the latest block ended, every log linked to it
    and so locked. Same refusals as billing one log."""
    project = _owned_project(id)
    back = redirect(url_for('projects.detail', id=project.id))
    if project.rate_type != 'hourly':
        flash('Only hourly projects bill by the hour.', 'error')
        return back
    if not project.rate or project.rate <= 0:
        flash('Set an hourly rate on the project first.', 'error')
        return back
    logs = project.unbilled_time_logs
    if not logs:
        flash('No unbilled hours on this project.', 'error')
        return back

    hours = sum(log.hours for log in logs)
    started = min(log.started_on for log in logs)
    ended = max(log.ended_on for log in logs)
    tx = Transaction(
        user_id=current_user.id,
        amount=hours * project.rate,
        date=ended,
        kind=INCOME,
        category='Freelance Project',
        description=f'{hours:g}h on {project.name} ({TimeLog.label_for(started, ended)})',
        is_tax_deductible=False,
        source='project')
    for log in logs:
        log.transaction = tx
    db.session.commit()
    flash(f'Income of {hours * project.rate:.2f} booked for {hours:g}h across {len(logs)} entries.', 'success')
    return back


# --- Documents -----------------------------------------------------------
#
# There is no per-admin ownership check here: @login_required is the whole
# access rule on this side, and every admin works the same books (ADR-0014).
# The client-facing half of the rule - what a portal account may see - arrives
# with the portal, and stays scoped there.

def _owned_project(id):
    """found or 404; every admin sees every project (ADR-0014)."""
    project = db.session.get(Project, id)
    if not project:
        abort(404)
    return project


def _owned_document(doc_id):
    """found or 404; every admin sees every project (ADR-0014)."""
    doc = db.session.get(ProjectDocument, doc_id)
    if not doc:
        abort(404)
    return doc


def _owned_time_log(log_id):
    """found or 404; every admin sees every project (ADR-0014)."""
    log = db.session.get(TimeLog, log_id)
    if not log:
        abort(404)
    return log


@projects_bp.route('/<int:id>')
@login_required
def detail(id):
    project = _owned_project(id)
    inventory_items = (InventoryItem.query.filter_by(project_id=project.id)
                       .join(InventoryItem.transaction)
                       .order_by(Transaction.date.desc()).all())
    return render_template('projects/detail.html',
        project=project,
        # By day, then by entry: ended_on carries a time only for migrated
        # rows, so the clock must not decide the order within a day.
        time_logs=TimeLog.query.filter_by(project_id=project.id)
                              .order_by(db.func.date(TimeLog.ended_on).desc(),
                                        TimeLog.id.desc()).all(),
        inventory_items=inventory_items,
        inventory_total=sum(i.total_cost for i in inventory_items),
        documents=ProjectDocument.query.filter_by(project_id=project.id)
                                       .order_by(ProjectDocument.created_at.desc()).all(),
        clients=Client.query.filter_by(is_active=True)
                            .order_by(Client.name).all(),
        max_upload_mb=documents.MAX_UPLOAD_BYTES // (1024 * 1024),
        now=datetime.now(),
        currency=Business.get().currency)


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

    # Only ids that are real clients. Filtering rather than validating means an
    # id that is not a client at all is dropped, not honoured.
    requested = {int(v) for v in request.form.getlist('client_ids') if v.isdigit()}
    allowed = {c.id for c in Client.query.all()}
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
