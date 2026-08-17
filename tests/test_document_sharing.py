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
import os

import pytest

import gigledger.app
import gigledger.documents
import gigledger.portal_auth as portal_auth
from gigledger.app import create_app
from gigledger.models import (Client, DocumentAccess, DocumentShare, PortalAccount,
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
    return build_app(tmp_path, monkeypatch)


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
    http.post(f'/portal/invite/{token}',
              data={'password': 'portal-pass-1234', 'confirm_password': 'portal-pass-1234'},
              follow_redirects=True)
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

def test_a_document_cannot_be_shared_with_another_users_client(app):
    doc_id = a_document(app)
    with app.app_context():
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        their_client = Client(user_id=stranger.id, name='Not Yours')
        db.session.add(their_client)
        db.session.commit()
        their_client_id = their_client.id

    share(app, doc_id, [their_client_id])

    with app.app_context():
        assert DocumentShare.query.count() == 0


def test_a_freelancer_cannot_share_another_users_document(app):
    client_id = a_client_id(app)
    doc_id = a_document(app)
    with app.app_context():
        stranger = User(email='stranger@example.com', password_hash='x')
        db.session.add(stranger)
        db.session.commit()
        stranger_id = stranger.id

    freelancer(app, stranger_id).post(f'/projects/documents/{doc_id}/share',
                                      data={'client_ids': [str(client_id)]})

    with app.app_context():
        assert DocumentShare.query.count() == 0


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


def test_documents_from_two_freelancers_are_labelled_separately(app):
    """PortalAccount is global, so one page can carry two tenants' material.
    They must not render as one undifferentiated list (ADR-0008)."""
    first_client = a_client_id(app)
    doc_id = a_document(app)
    share(app, doc_id, [first_client])
    http = portal_for(app, first_client, email='shared@example.com')

    with app.app_context():
        other_user = User(email='other@example.com', password_hash='x',
                          business_name='Second Studio')
        db.session.add(other_user)
        db.session.commit()
        other_client = Client(user_id=other_user.id, name='Same Person',
                              email='shared@example.com')
        db.session.add(other_client)
        db.session.commit()
        other_project = Project(user_id=other_user.id, client_id=other_client.id,
                                name='Other Project')
        db.session.add(other_project)
        db.session.commit()
        other_doc = ProjectDocument(user_id=other_user.id, project_id=other_project.id,
                                    kind='link', title='Other Brief',
                                    external_url='https://example.com/brief',
                                    provider='other')
        db.session.add(other_doc)
        db.session.commit()
        account = PortalAccount.query.filter_by(email='shared@example.com').one()
        other_client.portal_account_id = account.id
        db.session.add(DocumentShare(document_id=other_doc.id, client_id=other_client.id))
        db.session.commit()

    body = http.get('/portal/').get_data(as_text=True)

    assert 'Signed contract' in body
    assert 'Other Brief' in body
    assert 'Second Studio' in body
    assert 'Demo Freelance Studio' in body


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
