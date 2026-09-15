"""
GigLedger - Project document storage.

Everything that touches the filesystem or a client-supplied URL lives here, so
the rules are in one place rather than spread across route handlers. See
docs/adr/0006.

Two rules this module exists to enforce:

* **The filesystem never sees a name a user chose.** Uploads are written under
  a generated token; the name the user recognises is a column. A name that
  reaches `os.path.join` is a name that can contain `../`.
* **Nothing stored here is ever rendered by a browser from this origin.**
  Downloads are attachments with a generic content type, and the extension
  allowlist is backed by an explicit rejection list, so the dangerous types stay
  refused even if the allowlist grows.
"""
import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

# Uploaded bytes live in the repository root, one level above this package,
# beside the database and for the same reason: user data does not belong inside
# the importable source tree. Derived once and shared, so that a test can point
# it somewhere disposable and every writer follows. See ADR-0002 for the
# identical reasoning applied to DB_PATH.
UPLOAD_ROOT = os.path.join(
    os.path.dirname(os.path.abspath(os.path.dirname(__file__))),
    'uploads',
)

# 25 MB. Enforced by Flask via MAX_CONTENT_LENGTH before a request body is
# read, so an oversized upload never reaches disk.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.odt', '.rtf',
    '.xls', '.xlsx', '.ods', '.csv',
    '.ppt', '.pptx', '.odp',
    '.txt', '.md',
    '.png', '.jpg', '.jpeg', '.gif', '.webp',
    '.zip',
}

# Refused by name as well as by omission from the allowlist above. These are
# the extensions a browser will execute if it is ever persuaded to render the
# response inline, and the allowlist protects against them only for as long as
# nobody adds them. Naming them makes that an argument someone has to have.
REJECTED_EXTENSIONS = {'.html', '.htm', '.xhtml', '.shtml', '.svg', '.xml'}

# A link document's URL is interpolated into an href. Autoescaping does not make
# `javascript:` inert, so the scheme is constrained at the write - the same
# treatment the Constrained Columns get in the route modules.
ALLOWED_URL_SCHEMES = {'http', 'https'}

DRIVE_HOSTS = {'docs.google.com', 'drive.google.com', 'sheets.google.com'}

KINDS = {'upload', 'link'}
PROVIDERS = {'google_drive', 'other'}

# How long a document counts as "new" on the owner's own project cards. A cue,
# not a notification: the owner added the file themselves, so there is no
# state worth keeping about whether they have "seen" it. See ADR-0012.
RECENTLY_ADDED = timedelta(days=7)


def extension_of(filename):
    return os.path.splitext(filename or '')[1].lower()


def is_allowed_upload(filename):
    ext = extension_of(filename)
    if ext in REJECTED_EXTENSIONS:
        return False
    return ext in ALLOWED_EXTENSIONS


def clean_external_url(value):
    """The URL if it is one we are willing to put in an href, else None."""
    parts = urlsplit((value or '').strip())
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES or not parts.netloc:
        return None
    return parts.geturl()


def provider_of(url):
    host = urlsplit(url).hostname or ''
    return 'google_drive' if host.lower() in DRIVE_HOSTS else 'other'


def path_for(stored_name):
    """Absolute path of a stored file.

    `stored_name` is a token this module generated, never user input, which is
    what makes the join safe. The basename call is belt-and-braces against a
    future caller passing something else.
    """
    return os.path.join(UPLOAD_ROOT, os.path.basename(stored_name))


def store(file_storage):
    """Write an upload under a generated name. Returns (stored_name, byte_size).

    The extension is carried over from the *validated* allowlist so the upload
    directory stays inspectable by a human; the rest of the name is discarded.
    """
    stored_name = f"{uuid.uuid4().hex}{extension_of(file_storage.filename)}"
    os.makedirs(UPLOAD_ROOT, exist_ok=True)
    path = path_for(stored_name)
    file_storage.save(path)
    return stored_name, os.path.getsize(path)


def documents_shared_with(clients):
    """Every document granted to any of these Client rows.

    The portal's only way in. Deliberately expressed as "granted to a client I
    hold" rather than "belonging to a project I am on": the grant is the access
    rule, and a project relationship is not one. See ADR-0006.
    """
    from .models import DocumentShare, ProjectDocument

    client_ids = [c.id for c in clients]
    if not client_ids:
        return []
    return (ProjectDocument.query
            .join(DocumentShare, DocumentShare.document_id == ProjectDocument.id)
            .filter(DocumentShare.client_id.in_(client_ids))
            .order_by(ProjectDocument.created_at.desc())
            .distinct()
            .all())


def per_project_stats(user_id):
    """{project_id: (document count, most recent created_at)} for one owner.

    One grouped query rather than `project.documents` per card: the list page
    renders every project the user has, and a lazy load per card is a query
    per card.
    """
    from sqlalchemy import func
    from .models import ProjectDocument

    rows = (ProjectDocument.query
            .with_entities(ProjectDocument.project_id,
                           func.count(ProjectDocument.id),
                           func.max(ProjectDocument.created_at))
            .filter_by(user_id=user_id)
            .group_by(ProjectDocument.project_id)
            .all())
    return {project_id: (count, latest) for project_id, count, latest in rows}


def recent_cutoff(now=None):
    return (now or datetime.utcnow()) - RECENTLY_ADDED


def newly_shared_ids(clients, since):
    """Ids of documents granted to any of these clients after `since`.

    Computed from the grant, not the document: the grant is what gives a
    client access (ADR-0006), so the grant is what makes a document new *to
    them*. `since=None` is a first visit, and everything counts.
    """
    from .models import DocumentShare

    client_ids = [c.id for c in clients]
    if not client_ids:
        return set()
    query = DocumentShare.query.filter(DocumentShare.client_id.in_(client_ids))
    if since is not None:
        query = query.filter(DocumentShare.created_at > since)
    return {share.document_id for share in query.all()}


def mark_documents_seen(account):
    """Stamp the portal account's visit to its document list.

    Called from a GET, like `record_access` below, and for the same reason:
    this records something the reader did, not something they asked for. A
    forged cross-site GET could at worst clear a "new" badge the client had
    not yet read - the same class of harm as a forged download writing an
    access row - and the response is unreadable to the forger either way.
    """
    from .models import db

    account.documents_seen_at = datetime.utcnow()
    db.session.commit()


def is_shared_with(document, clients):
    return document.shared_client_ids & {c.id for c in clients}


def record_access(document, user_id=None, portal_account_id=None):
    """Called only after authorisation succeeds. A refused request is not an
    access and must not be recorded as one."""
    from flask import request
    from .models import DocumentAccess, db

    db.session.add(DocumentAccess(document_id=document.id,
                                  user_id=user_id,
                                  portal_account_id=portal_account_id,
                                  ip=(request.remote_addr or '')[:64]))
    db.session.commit()


def delete(stored_name):
    """Remove a stored file. Missing is success: this runs during row deletion,
    and a file already gone must not block the row from going too."""
    if not stored_name:
        return
    try:
        os.remove(path_for(stored_name))
    except FileNotFoundError:
        pass
