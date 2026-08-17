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


def delete(stored_name):
    """Remove a stored file. Missing is success: this runs during row deletion,
    and a file already gone must not block the row from going too."""
    if not stored_name:
        return
    try:
        os.remove(path_for(stored_name))
    except FileNotFoundError:
        pass
