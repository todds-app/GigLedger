"""Project document storage and serving.

The decisions under test are recorded in docs/adr/0006. The ones worth stating
here, because a reader of the tests should not have to infer them:

1. **Uploaded bytes are never rendered by a browser from this origin.** Every
   download is an attachment with a generic content type, and the extension
   allowlist additionally names `.html`/`.svg` as rejected. Both are tested,
   because the allowlist protects by omission and omission is what erodes when
   somebody adds a type later.
2. **The filesystem never sees a client-controlled name.** The original filename
   is data in a column; the file on disk is an opaque token.
3. **A link document's URL ends up in an `href`.** Autoescape does not make
   `javascript:` safe, so the scheme is constrained at the write - the same
   shape as the `clean_*` helpers already used for the Constrained Columns.
4. **Cascade here is a property of the code, not of the schema.** SQLite's
   foreign keys are not enforced in this app (ADR-0006), so deleting a project
   has to be tested for what it leaves behind - rows *and* bytes.
"""
import io
from datetime import datetime, timedelta
import os

import pytest

import gigledger.app
import gigledger.documents
from gigledger.app import create_app
from gigledger.models import Project, ProjectDocument, User, db


def build_app(tmp_path, monkeypatch, **config):
    """An app backed by a throwaway database and a throwaway upload root.

    Both matter: create_app() otherwise resolves to the real gigledger.db, and
    a failing run - one that wrongly writes or deletes - would act on the
    user's own data and documents.
    """
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(gigledger.documents, 'UPLOAD_ROOT', str(tmp_path / 'uploads'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key',
                      WTF_CSRF_ENABLED=False, **config)
    app.login_manager.session_protection = None
    return app


@pytest.fixture
def seeded_app(tmp_path, monkeypatch):
    """The app exactly as it starts up, demo documents included."""
    return build_app(tmp_path, monkeypatch)


def clear_documents(app):
    with app.app_context():
        for doc in ProjectDocument.query.all():
            if doc.stored_name:
                gigledger.documents.delete(doc.stored_name)
            db.session.delete(doc)
        db.session.commit()
    return app


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The app with the demo documents cleared away.

    The seed ships example documents so a new user sees both kinds without
    creating one. Every test below is about what happens to a document a test
    itself created, so starting from an empty table keeps assertions like
    `count() == 0` meaning "nothing was created" rather than "nothing beyond the
    fixtures". The seed is covered on its own, against `seeded_app`.
    """
    return clear_documents(build_app(tmp_path, monkeypatch))


def login(app, user_id=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
    return client


def a_project(app, user_id=1):
    with app.app_context():
        return Project.query.filter_by(user_id=user_id).first().id


def upload(client, project_id, filename, content=b'%PDF-1.4 fake', title='A document'):
    return client.post(
        f'/projects/{project_id}/documents/upload',
        data={'title': title, 'file': (io.BytesIO(content), filename)},
        content_type='multipart/form-data',
        follow_redirects=True,
    )


# --- the allowlist -------------------------------------------------------

@pytest.mark.parametrize('filename', ['payload.html', 'payload.htm',
                                      'payload.svg', 'payload.xhtml'])
def test_upload_rejects_browser_renderable_files(app, filename):
    """These are the extensions that turn into stored XSS the moment anyone
    changes the disposition. Rejected by name, not merely absent from the
    allowlist."""
    client = login(app)
    pid = a_project(app)

    upload(client, pid, filename)

    with app.app_context():
        assert ProjectDocument.query.count() == 0


def test_upload_rejects_an_extension_that_is_simply_not_allowed(app):
    client = login(app)
    pid = a_project(app)

    upload(client, pid, 'installer.exe')

    with app.app_context():
        assert ProjectDocument.query.count() == 0


@pytest.mark.parametrize('filename', ['contract.pdf', 'budget.xlsx', 'notes.md'])
def test_upload_accepts_document_and_spreadsheet_types(app, filename):
    client = login(app)
    pid = a_project(app)

    upload(client, pid, filename)

    with app.app_context():
        doc = ProjectDocument.query.one()
        assert doc.kind == 'upload'
        assert doc.original_name == filename


# --- storage -------------------------------------------------------------

def test_the_name_on_disk_is_opaque(app):
    """The original filename is client-controlled text. It lives in a column;
    it never reaches the filesystem."""
    client = login(app)
    pid = a_project(app)

    upload(client, pid, 'Q3 budget (final) v2.pdf')

    with app.app_context():
        doc = ProjectDocument.query.one()
        assert doc.original_name == 'Q3 budget (final) v2.pdf'
        assert 'budget' not in doc.stored_name
        assert os.listdir(gigledger.documents.UPLOAD_ROOT) == [doc.stored_name]


def test_upload_records_the_byte_size(app):
    client = login(app)
    pid = a_project(app)

    upload(client, pid, 'contract.pdf', content=b'x' * 1234)

    with app.app_context():
        assert ProjectDocument.query.one().byte_size == 1234


def test_the_upload_cap_is_wired_to_the_configured_limit(app):
    """The cap has to be enforced by Flask rather than checked in the route:
    a route-level check runs after the body has already been read."""
    assert app.config['MAX_CONTENT_LENGTH'] == gigledger.documents.MAX_UPLOAD_BYTES


def test_an_oversized_upload_is_refused_before_it_reaches_disk(tmp_path, monkeypatch):
    """Shrinks the *module's* limit rather than setting MAX_CONTENT_LENGTH on
    the test app: setting the config directly would prove Flask enforces a cap,
    which was never in doubt, while saying nothing about whether this app sets
    one. Patched before create_app so the real wiring is what carries it."""
    monkeypatch.setattr(gigledger.documents, 'MAX_UPLOAD_BYTES', 500)
    app = clear_documents(build_app(tmp_path, monkeypatch))
    client = login(app)
    pid = a_project(app)

    response = upload(client, pid, 'contract.pdf', content=b'x' * 5000)

    assert response.status_code == 413
    with app.app_context():
        assert ProjectDocument.query.count() == 0
    assert os.listdir(gigledger.documents.UPLOAD_ROOT) == []


# --- serving -------------------------------------------------------------

def test_download_is_an_attachment_with_a_generic_type(app):
    """Never inline, and never a type the browser will try to render, whatever
    the file actually claims to be."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf', content=b'hello')

    with app.app_context():
        doc_id = ProjectDocument.query.one().id
    response = client.get(f'/projects/documents/{doc_id}/download')

    assert response.status_code == 200
    assert response.data == b'hello'
    assert response.headers['Content-Type'].startswith('application/octet-stream')
    assert response.headers['Content-Disposition'].startswith('attachment')


def test_download_refuses_another_users_document(app):
    """The ownership filter is the whole access control story in commit 1."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        doc_id = ProjectDocument.query.one().id
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        stranger_id = stranger.id

    response = login(app, stranger_id).get(f'/projects/documents/{doc_id}/download')

    assert response.status_code == 404


def test_bytes_missing_from_disk_are_a_404_not_a_500(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        doc = ProjectDocument.query.one()
        doc_id = doc.id
        os.remove(os.path.join(gigledger.documents.UPLOAD_ROOT, doc.stored_name))

    assert client.get(f'/projects/documents/{doc_id}/download').status_code == 404


# --- link documents ------------------------------------------------------

@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,<script>',
                                 'file:///etc/passwd', 'vbscript:msgbox'])
def test_link_rejects_a_url_that_is_not_http(app, url):
    """The URL is rendered into an href. Autoescape does not defuse a
    javascript: scheme - constraining the scheme at the write does."""
    client = login(app)
    pid = a_project(app)

    client.post(f'/projects/{pid}/documents/link',
                data={'title': 'Sheet', 'url': url}, follow_redirects=True)

    with app.app_context():
        assert ProjectDocument.query.count() == 0


def test_link_accepts_an_https_drive_url(app):
    client = login(app)
    pid = a_project(app)
    url = 'https://docs.google.com/spreadsheets/d/abc123/edit'

    client.post(f'/projects/{pid}/documents/link',
                data={'title': 'Budget', 'url': url}, follow_redirects=True)

    with app.app_context():
        doc = ProjectDocument.query.one()
        assert doc.kind == 'link'
        assert doc.external_url == url
        assert doc.stored_name is None


# --- deletion ------------------------------------------------------------

def test_deleting_a_project_deletes_its_documents_and_their_bytes(app):
    """SQLite is not enforcing the foreign key, and an ORM cascade does not
    touch the filesystem. Both halves are asserted because both are hand-written."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        path = os.path.join(gigledger.documents.UPLOAD_ROOT,
                            ProjectDocument.query.one().stored_name)
    assert os.path.exists(path)

    client.post(f'/projects/delete/{pid}', follow_redirects=True)

    with app.app_context():
        assert ProjectDocument.query.count() == 0
    assert not os.path.exists(path)


def test_deleting_a_document_deletes_its_bytes(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        doc = ProjectDocument.query.one()
        doc_id, path = doc.id, os.path.join(gigledger.documents.UPLOAD_ROOT,
                                            doc.stored_name)

    client.post(f'/projects/documents/{doc_id}/delete', follow_redirects=True)

    with app.app_context():
        assert ProjectDocument.query.count() == 0
    assert not os.path.exists(path)


def test_delete_refuses_another_users_document(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    with app.app_context():
        doc_id = ProjectDocument.query.one().id
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        stranger_id = stranger.id

    login(app, stranger_id).post(f'/projects/documents/{doc_id}/delete',
                                 follow_redirects=True)

    with app.app_context():
        assert ProjectDocument.query.count() == 1


# --- demo data -----------------------------------------------------------

def test_the_demo_seeds_both_kinds_of_document(seeded_app):
    """Both kinds, so the difference between a stored file and a reference is
    visible without anyone having to create one."""
    with seeded_app.app_context():
        kinds = {d.kind for d in ProjectDocument.query.all()}
        assert kinds == {'upload', 'link'}


def test_the_seeded_upload_has_bytes_on_disk(seeded_app):
    with seeded_app.app_context():
        doc = ProjectDocument.query.filter_by(kind='upload').first()
        assert os.path.exists(gigledger.documents.path_for(doc.stored_name))
        assert doc.byte_size > 0


def test_the_demo_does_not_seed_a_working_portal_login(seeded_app):
    """The demo password is public. A seeded portal credential would be a second
    known password on an externally-facing login - and making the explorer
    redeem an invite demonstrates the flow they most need to understand."""
    from gigledger.models import Client, PortalAccount

    with seeded_app.app_context():
        assert PortalAccount.query.count() == 0
        assert Client.query.filter(Client.portal_account_id.isnot(None)).count() == 0


# --- the detail page -----------------------------------------------------

def test_project_detail_lists_the_projects_documents(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf', title='Signed contract')

    body = client.get(f'/projects/{pid}').get_data(as_text=True)

    assert 'Signed contract' in body


def test_project_detail_refuses_another_users_project(app):
    pid = a_project(app)
    with app.app_context():
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        stranger_id = stranger.id

    assert login(app, stranger_id).get(f'/projects/{pid}').status_code == 404


# --- the projects list ---------------------------------------------------
#
# The Documents section lives on the detail page, which nothing on the list
# page mentioned. Each card now carries a count and a link into the section,
# and a recency cue for anything added in the last week. The cue is a cue,
# not a notification: the owner added the file themselves.

def documents_row(app, project_id, user_id=1):
    """The card's documents link and whatever sits beside it."""
    body = login(app, user_id).get('/projects/').get_data(as_text=True)
    start = body.index(f'/projects/{project_id}#documents')
    end = body.index('</div>', start)
    return body[start:end]


def test_the_projects_list_links_into_an_empty_documents_section(app):
    pid = a_project(app)

    row = documents_row(app, pid)

    assert 'No documents' in row


def test_the_projects_list_counts_uploads_and_links_together(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')
    client.post(f'/projects/{pid}/documents/link',
                data={'title': 'Budget', 'url': 'https://docs.google.com/x'},
                follow_redirects=True)

    assert '2 documents' in documents_row(app, pid)


def test_a_document_added_this_week_is_flagged_on_the_card(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')

    assert 'New this week' in documents_row(app, pid)


def test_a_document_older_than_a_week_is_not_flagged(app):
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')
    with app.app_context():
        doc = ProjectDocument.query.one()
        doc.created_at = datetime.utcnow() - timedelta(days=8)
        db.session.commit()

    row = documents_row(app, pid)

    assert '1 document' in row
    assert 'New this week' not in row


def test_another_users_documents_do_not_count_on_my_list(app):
    """The aggregate is filtered by owner like every other query, not by
    project id alone - a project id is guessable."""
    client = login(app)
    pid = a_project(app)
    upload(client, pid, 'contract.pdf')
    with app.app_context():
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        db.session.add(Project(user_id=stranger.id, name='Theirs', rate=1))
        db.session.commit()
        stranger_id = stranger.id
        their_pid = Project.query.filter_by(user_id=stranger_id).one().id

    assert 'No documents' in documents_row(app, their_pid, user_id=stranger_id)


def test_a_recent_client_added_document_is_flagged_as_from_a_client(app):
    """The owner did not add it, so the pill says where it came from rather
    than merely that it is recent."""
    pid = a_project(app)
    with app.app_context():
        db.session.add(ProjectDocument(user_id=1, project_id=pid, kind='link',
                                       title='From the client', external_url='https://x.example',
                                       provider='other', added_by_client_id=1))
        db.session.commit()

    row = documents_row(app, pid)

    assert 'New from client' in row
    assert 'New this week' not in row
