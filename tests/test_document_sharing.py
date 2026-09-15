"""Sharing documents with clients, and what the portal exposes.

This is where the two halves built in the previous commits meet, so it is where
the access rule stated in docs/adr/0006 finally becomes enforceable: a document
is private to its owner until it is deliberately granted to a named client.

Three properties this file exists to hold:

1. **Default private.** A document nobody shared is visible to nobody. A
   mis-click leaks nothing because there is nothing to mis-click into.
2. **The portal shows the project's name and nothing else about it.** A project
   carries `rate`, `hours_logged` and `description` - pricing and internal notes
   that a client granted one document must not read as a side effect.
3. **A portal account is global, so its page mixes tenants by construction.**
   Documents from two different freelancers must never render as one
   undifferentiated list (ADR-0008).
"""
import io
import sqlite3
import os

import pytest

import gigledger.app
import gigledger.documents
import gigledger.portal_auth as portal_auth
from gigledger.app import create_app
from gigledger.models import (Business, Client, DocumentAccess, DocumentShare, PortalAccount,
                              Project, ProjectDocument, User, db)


def build_app(tmp_path, monkeypatch, **config):
    monkeypatch.setattr(gigledger.app, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.setattr(gigledger.documents, 'UPLOAD_ROOT', str(tmp_path / 'uploads'))
    app = create_app()
    app.config.update(TESTING=True, SECRET_KEY='test-key',
                      WTF_CSRF_ENABLED=False, **config)
    app.login_manager.session_protection = None
    return app


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Demo documents cleared away, as in tests/test_documents.py: every test
    here is about a document the test itself created and shared, and the seeded
    examples would only make the counts ambiguous."""
    app = build_app(tmp_path, monkeypatch)
    with app.app_context():
        for doc in ProjectDocument.query.all():
            if doc.stored_name:
                gigledger.documents.delete(doc.stored_name)
            db.session.delete(doc)
        db.session.commit()
    return app


def freelancer(app, user_id=1):
    http = app.test_client()
    with http.session_transaction() as session:
        session['_user_id'] = str(user_id)
        session['_fresh'] = True
    return http


def a_project(app, user_id=1):
    with app.app_context():
        return Project.query.filter_by(user_id=user_id).first().id


def a_client_id(app, user_id=1):
    with app.app_context():
        return Client.query.filter_by(user_id=user_id).first().id


def a_document(app, project_id=None, filename='contract.pdf', title='Signed contract'):
    project_id = project_id or a_project(app)
    freelancer(app).post(
        f'/projects/{project_id}/documents/upload',
        data={'title': title, 'file': (io.BytesIO(b'the contract'), filename)},
        content_type='multipart/form-data', follow_redirects=True)
    with app.app_context():
        return ProjectDocument.query.order_by(ProjectDocument.id.desc()).first().id


def share(app, doc_id, client_ids):
    return freelancer(app).post(f'/projects/documents/{doc_id}/share',
                                data={'client_ids': [str(c) for c in client_ids]},
                                follow_redirects=True)


def portal_for(app, client_id, email='billing@acmecorp.com'):
    """A signed-in portal session for a client, via the real invite flow."""
    with app.app_context():
        client = db.session.get(Client, client_id)
        _, token = portal_auth.create_invite(client, email)
        db.session.commit()
    http = app.test_client()
    # Not following the redirect: redeeming lands on the portal home, and
    # loading the home is a "visit" the freshness tests below need to control.
    http.post(f'/portal/invite/{token}',
              data={'password': 'portal-pass-1234', 'confirm_password': 'portal-pass-1234'})
    return http


# --- default private -----------------------------------------------------

def test_a_document_is_shared_with_nobody_by_default(app):
    client_id = a_client_id(app)
    a_document(app)
    http = portal_for(app, client_id)

    assert 'Signed contract' not in http.get('/portal/').get_data(as_text=True)


def test_an_unshared_document_cannot_be_downloaded_from_the_portal(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    http = portal_for(app, client_id)

    assert http.get(f'/portal/documents/{doc_id}/download').status_code == 404


# --- granting ------------------------------------------------------------

def test_a_shared_document_appears_in_the_portal(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert 'Signed contract' in body


def test_a_shared_document_can_be_downloaded_from_the_portal(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])

    response = portal_for(app, client_id).get(f'/portal/documents/{doc_id}/download')

    assert response.status_code == 200
    assert response.data == b'the contract'
    assert response.headers['Content-Type'].startswith('application/octet-stream')
    assert response.headers['Content-Disposition'].startswith('attachment')


def test_a_document_shared_with_one_client_is_invisible_to_another(app):
    with app.app_context():
        first, second = Client.query.filter_by(user_id=1).limit(2).all()
        first_id, second_id = first.id, second.id
    doc_id = a_document(app)
    share(app, doc_id, [first_id])

    http = portal_for(app, second_id, email='other@example.com')

    assert 'Signed contract' not in http.get('/portal/').get_data(as_text=True)
    assert http.get(f'/portal/documents/{doc_id}/download').status_code == 404


def test_unsharing_removes_access_immediately(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])
    http = portal_for(app, client_id)
    assert http.get(f'/portal/documents/{doc_id}/download').status_code == 200

    share(app, doc_id, [])

    assert http.get(f'/portal/documents/{doc_id}/download').status_code == 404


def test_revoking_portal_access_removes_the_documents_with_it(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])
    portal_for(app, client_id)

    with app.app_context():
        portal_auth.revoke(db.session.get(Client, client_id))
        db.session.commit()

    fresh = app.test_client()
    fresh.post('/portal/login', data={'email': 'billing@acmecorp.com',
                                      'password': 'portal-pass-1234'})
    assert 'Signed contract' not in fresh.get('/portal/', follow_redirects=True).get_data(as_text=True)


# --- the owner's side ----------------------------------------------------

def test_a_document_can_be_shared_with_a_client_another_admin_added(app):
    doc_id = a_document(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        their_client = Client(user_id=colleague.id, name='Added By Colleague')
        db.session.add(their_client)
        db.session.commit()
        their_client_id = their_client.id

    share(app, doc_id, [their_client_id])

    with app.app_context():
        assert DocumentShare.query.filter_by(client_id=their_client_id).count() == 1


def test_any_admin_can_share_a_document(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    with app.app_context():
        colleague = User(email='colleague@example.com', password_hash='x')
        db.session.add(colleague)
        db.session.commit()
        colleague_id = colleague.id

    freelancer(app, colleague_id).post(f'/projects/documents/{doc_id}/share',
                                       data={'client_ids': [str(client_id)]})

    with app.app_context():
        assert DocumentShare.query.count() == 1


def test_sharing_the_same_document_twice_does_not_duplicate_the_grant(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)

    share(app, doc_id, [client_id])
    share(app, doc_id, [client_id])

    with app.app_context():
        assert DocumentShare.query.count() == 1


# --- what the portal does not show ---------------------------------------

def test_the_portal_shows_the_project_name_but_not_its_commercials(app):
    """A client granted one document must not learn the rate, the hours logged,
    or the internal description as a side effect of the grouping."""
    project_id = a_project(app)
    with app.app_context():
        project = db.session.get(Project, project_id)
        project.description = 'INTERNAL-ONLY-NOTE'
        project.rate = 98765
        project.hours_logged = 4321
        project_name = project.name
        db.session.commit()
    client_id = a_client_id(app)
    doc_id = a_document(app, project_id)
    share(app, doc_id, [client_id])

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert project_name in body
    assert 'INTERNAL-ONLY-NOTE' not in body
    assert '98765' not in body
    assert '4321' not in body


def test_the_portal_names_the_business_once_and_groups_by_project(app):
    """One business per install (ADR-0014): the heading is the business name,
    then projects, then documents. Nothing is grouped by who uploaded it."""
    first_client = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [first_client])
    http = portal_for(app, first_client, email='shared@example.com')

    with app.app_context():
        Business.get().name = 'Dani Smith Design'
        db.session.commit()
        other_project = Project(user_id=1, client_id=first_client, name='Other Project')
        db.session.add(other_project)
        db.session.commit()
        other_doc = ProjectDocument(user_id=1, project_id=other_project.id,
                                    kind='link', title='Other Brief',
                                    external_url='https://example.com/brief',
                                    provider='other')
        db.session.add(other_doc)
        db.session.commit()
        db.session.add(DocumentShare(document_id=other_doc.id, client_id=first_client))
        db.session.commit()

    body = http.get('/portal/').get_data(as_text=True)

    # One <h2> heading. The name also appears inside each project's upload
    # modal copy ("… will see it straight away"), so count the heading markup.
    assert body.count('<h2 class="text-sm font-bold text-gray-700">Dani Smith Design</h2>') == 1
    assert 'Signed contract' in body
    assert 'Other Brief' in body
    assert 'Other Project' in body


def test_a_shared_link_document_is_shown_as_a_link(app):
    project_id = a_project(app)
    client_id = a_client_id(app)
    freelancer(app).post(f'/projects/{project_id}/documents/link',
                         data={'title': 'Budget sheet',
                               'url': 'https://docs.google.com/spreadsheets/d/abc/edit'},
                         follow_redirects=True)
    with app.app_context():
        doc_id = ProjectDocument.query.filter_by(kind='link').one().id
    share(app, doc_id, [client_id])

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert 'https://docs.google.com/spreadsheets/d/abc/edit' in body


def test_a_link_document_has_no_portal_download(app):
    """There are no bytes to serve. The portal must not offer a download that
    would 500, and must not become a proxy that fetches the far side."""
    project_id = a_project(app)
    client_id = a_client_id(app)
    freelancer(app).post(f'/projects/{project_id}/documents/link',
                         data={'title': 'Budget sheet', 'url': 'https://example.com/x'},
                         follow_redirects=True)
    with app.app_context():
        doc_id = ProjectDocument.query.filter_by(kind='link').one().id
    share(app, doc_id, [client_id])

    assert portal_for(app, client_id).get(
        f'/portal/documents/{doc_id}/download').status_code == 404


# --- the access log ------------------------------------------------------

def test_a_portal_download_is_recorded(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])

    portal_for(app, client_id).get(f'/portal/documents/{doc_id}/download')

    with app.app_context():
        entry = DocumentAccess.query.one()
        assert entry.document_id == doc_id
        assert entry.portal_account_id is not None
        assert entry.user_id is None


def test_an_owner_download_is_recorded(app):
    doc_id = a_document(app)

    freelancer(app).get(f'/projects/documents/{doc_id}/download')

    with app.app_context():
        entry = DocumentAccess.query.one()
        assert entry.user_id == 1
        assert entry.portal_account_id is None


def test_a_refused_download_is_not_recorded_as_access(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    http = portal_for(app, client_id)

    http.get(f'/portal/documents/{doc_id}/download')

    with app.app_context():
        assert DocumentAccess.query.count() == 0


# --- deletion still cleans up -------------------------------------------

def test_deleting_a_document_removes_its_shares(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])

    freelancer(app).post(f'/projects/documents/{doc_id}/delete', follow_redirects=True)

    with app.app_context():
        assert DocumentShare.query.count() == 0


def test_deleting_a_project_removes_its_documents_shares(app):
    project_id = a_project(app)
    client_id = a_client_id(app)
    doc_id = a_document(app, project_id)
    share(app, doc_id, [client_id])

    freelancer(app).post(f'/projects/delete/{project_id}', follow_redirects=True)

    with app.app_context():
        assert DocumentShare.query.count() == 0
        assert ProjectDocument.query.count() == 0


# --- what is new ---------------------------------------------------------
#
# The portal flags documents shared since the account last loaded its home
# page. "Since last visit" rather than "until opened", because opening a link
# document never touches GigLedger - a read receipt would be honest for
# uploads and silently absent for links. See ADR-0012.

def link_document(app, project_id, title='Budget sheet'):
    freelancer(app).post(f'/projects/{project_id}/documents/link',
                         data={'title': title,
                               'url': 'https://docs.google.com/spreadsheets/d/abc/edit'},
                         follow_redirects=True)
    with app.app_context():
        return ProjectDocument.query.filter_by(kind='link').one().id


def new_marker_count(body):
    return body.count('>New</span>')


def test_the_first_visit_flags_a_shared_document_and_stamps_the_visit(app):
    client_id = a_client_id(app)
    share(app, a_document(app), [client_id])
    http = portal_for(app, client_id)

    body = http.get('/portal/').get_data(as_text=True)

    assert new_marker_count(body) == 1
    assert '1 document shared with you since your last visit' in body
    with app.app_context():
        assert PortalAccount.query.one().documents_seen_at is not None


def test_a_second_visit_no_longer_flags_it(app):
    client_id = a_client_id(app)
    share(app, a_document(app), [client_id])
    http = portal_for(app, client_id)
    http.get('/portal/')

    body = http.get('/portal/').get_data(as_text=True)

    assert new_marker_count(body) == 0
    assert 'since your last visit' not in body


def test_only_documents_shared_after_the_last_visit_are_flagged(app):
    client_id = a_client_id(app)
    project_id = a_project(app)
    share(app, a_document(app, project_id, title='Old contract'), [client_id])
    http = portal_for(app, client_id)
    http.get('/portal/')
    share(app, a_document(app, project_id, filename='v2.pdf', title='New contract'),
          [client_id])

    body = http.get('/portal/').get_data(as_text=True)

    assert new_marker_count(body) == 1
    assert body.index('New contract') < body.index('>New</span>') < body.index('Old contract')


def test_resaving_the_same_share_does_not_reflag(app):
    """The share form posts the whole set; an unchanged grant keeps its
    timestamp, so saving again is not a new share."""
    client_id = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [client_id])
    http = portal_for(app, client_id)
    http.get('/portal/')
    share(app, doc_id, [client_id])

    assert new_marker_count(http.get('/portal/').get_data(as_text=True)) == 0


def test_a_link_document_is_flagged_like_an_upload(app):
    client_id = a_client_id(app)
    share(app, link_document(app, a_project(app)), [client_id])

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert new_marker_count(body) == 1


def test_freshness_is_per_portal_account(app):
    """Two people granted the same document each get their own 'since last
    visit'; one of them reading the page must not clear it for the other."""
    project_id = a_project(app)
    first_client = a_client_id(app)
    with app.app_context():
        second = Client(user_id=1, name='Second Person', email='second@example.com')
        db.session.add(second)
        db.session.commit()
        second_client = second.id
    doc_id = a_document(app, project_id)
    share(app, doc_id, [first_client, second_client])
    first = portal_for(app, first_client, email='first@example.com')
    second_http = portal_for(app, second_client, email='second@example.com')
    first.get('/portal/')

    assert new_marker_count(first.get('/portal/').get_data(as_text=True)) == 0
    assert new_marker_count(second_http.get('/portal/').get_data(as_text=True)) == 1


def test_the_migration_adds_documents_seen_at_to_an_existing_portal_accounts_table(tmp_path, monkeypatch):
    """Existing installs have a portal_accounts table without the column;
    create_all() does not alter tables, so _migrate_db() must."""
    db_path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE portal_accounts ("
                 "id INTEGER PRIMARY KEY, email VARCHAR(200), "
                 "password_hash VARCHAR(128), session_epoch INTEGER, "
                 "created_at DATETIME, last_login_at DATETIME)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)
    monkeypatch.setattr(gigledger.documents, 'UPLOAD_ROOT', str(tmp_path / 'uploads'))
    create_app()

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(portal_accounts)")}
    conn.close()
    assert 'documents_seen_at' in columns


# --- client uploads ------------------------------------------------------
#
# A portal client can add a file or a link to a project they are the client
# of. The row is the freelancer's document (user_id is theirs), attributed to
# the Client row, and shared back to that client in the same commit. Only the
# owner removes. See docs/superpowers/specs/2026-09-15-portal-client-uploads-design.md
# and ADR-0013.

def portal_upload(http, project_id, filename='floorplan.pdf', title='Floor plan',
                  content=b'%PDF-1.4 client'):
    return http.post(f'/portal/projects/{project_id}/documents/upload',
                     data={'title': title, 'file': (io.BytesIO(content), filename)},
                     content_type='multipart/form-data', follow_redirects=True)


def portal_link(http, project_id, url='https://docs.google.com/document/d/xyz/edit',
                title='Design board'):
    return http.post(f'/portal/projects/{project_id}/documents/link',
                     data={'title': title, 'url': url}, follow_redirects=True)


def test_a_client_can_upload_to_their_own_project(app):
    project_id = a_project(app)            # project 1, whose client is Acme Corp
    client_id = a_client_id(app)           # Acme Corp
    http = portal_for(app, client_id)

    response = portal_upload(http, project_id)

    assert response.status_code == 200
    with app.app_context():
        doc = ProjectDocument.query.one()
        assert doc.kind == 'upload'
        assert doc.project_id == project_id
        assert doc.user_id == 1                       # the freelancer's document
        assert doc.added_by_client_id == client_id    # attributed to the client
        assert doc.shared_client_ids == {client_id}   # and visible to them at once
        assert os.path.exists(gigledger.documents.path_for(doc.stored_name))


def test_a_client_can_add_a_link_to_their_own_project(app):
    project_id = a_project(app)
    client_id = a_client_id(app)

    portal_link(portal_for(app, client_id), project_id)

    with app.app_context():
        doc = ProjectDocument.query.one()
        assert doc.kind == 'link'
        assert doc.provider == 'google_drive'
        assert doc.user_id == 1
        assert doc.added_by_client_id == client_id
        assert doc.shared_client_ids == {client_id}


def test_a_client_cannot_upload_to_another_clients_project(app):
    """Project 2 belongs to StartupXYZ. Acme Corp's account gets the same 404
    the download route gives: which reason would itself be information."""
    with app.app_context():
        other_project = Project.query.filter_by(user_id=1, client_id=2).one().id
    http = portal_for(app, a_client_id(app))

    response = portal_upload(http, other_project)

    assert response.status_code == 404
    with app.app_context():
        assert ProjectDocument.query.count() == 0


def test_a_client_cannot_upload_to_a_project_with_no_client(app):
    project_id = a_project(app)
    client_id = a_client_id(app)
    with app.app_context():
        db.session.get(Project, project_id).client_id = None
        db.session.commit()

    response = portal_upload(portal_for(app, client_id), project_id)

    assert response.status_code == 404


def test_a_client_upload_obeys_the_same_allowlist(app):
    project_id = a_project(app)
    http = portal_for(app, a_client_id(app))

    portal_upload(http, project_id, filename='payload.html', content=b'<script>')

    with app.app_context():
        assert ProjectDocument.query.count() == 0
    assert os.listdir(gigledger.documents.UPLOAD_ROOT) == []


def test_a_client_link_obeys_the_same_scheme_rule(app):
    project_id = a_project(app)
    http = portal_for(app, a_client_id(app))

    portal_link(http, project_id, url='javascript:alert(1)')

    with app.app_context():
        assert ProjectDocument.query.count() == 0


def test_the_portal_has_no_way_to_delete_a_document(app):
    """Only the owner removes: a client who uploaded the wrong file asks."""
    project_id = a_project(app)
    http = portal_for(app, a_client_id(app))
    portal_upload(http, project_id)
    with app.app_context():
        doc_id = ProjectDocument.query.one().id

    assert http.post(f'/portal/documents/{doc_id}/delete').status_code in (404, 405)
    owner_route = http.post(f'/projects/documents/{doc_id}/delete', follow_redirects=False)
    assert owner_route.status_code in (302, 401, 403, 404)   # not the owner's session
    with app.app_context():
        assert ProjectDocument.query.count() == 1


def test_the_portal_lists_a_project_before_anything_is_shared(app):
    """A client cannot add to a project they cannot see, so the home page
    lists every project the account is the client of - by name only."""
    client_id = a_client_id(app)

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert 'Website Redesign' in body         # Acme Corp's project
    assert 'Monthly Retainer' not in body     # StartupXYZ's
    assert 'No documents yet' in body


def test_the_portal_does_not_list_a_project_with_no_client(app):
    project_id = a_project(app)
    client_id = a_client_id(app)
    with app.app_context():
        db.session.get(Project, project_id).client_id = None
        db.session.commit()

    body = portal_for(app, client_id).get('/portal/').get_data(as_text=True)

    assert 'Website Redesign' not in body


def test_the_owner_sees_who_added_a_document(app):
    project_id = a_project(app)
    portal_upload(portal_for(app, a_client_id(app)), project_id, title='Site survey')

    body = freelancer(app).get(f'/projects/{project_id}').get_data(as_text=True)

    assert 'Site survey' in body
    assert 'Added by Acme Corp' in body


def test_a_clients_own_upload_is_not_new_to_them(app):
    """It was not shared *with* them; greeting an uploader with "1 document
    shared with you since your last visit" would be wrong."""
    project_id = a_project(app)
    http = portal_for(app, a_client_id(app))
    portal_upload(http, project_id)

    body = http.get('/portal/').get_data(as_text=True)

    assert new_marker_count(body) == 0
    assert 'since your last visit' not in body


def test_the_migration_adds_added_by_client_id_to_an_existing_documents_table(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE project_documents ("
                 "id INTEGER PRIMARY KEY, user_id INTEGER, project_id INTEGER, "
                 "kind VARCHAR(20), title VARCHAR(200), stored_name VARCHAR(80), "
                 "original_name VARCHAR(255), byte_size INTEGER, external_url TEXT, "
                 "provider VARCHAR(20), created_at DATETIME, updated_at DATETIME)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(gigledger.app, 'DB_PATH', db_path)
    monkeypatch.setattr(gigledger.documents, 'UPLOAD_ROOT', str(tmp_path / 'uploads'))
    create_app()

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(project_documents)")}
    conn.close()
    assert 'added_by_client_id' in columns
