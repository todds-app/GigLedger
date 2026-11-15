"""Cards on the list pages.

The design system has no cards, but on the Projects and Clients lists each
entry is a box so one is easy to tell from the next, and the whole box opens
the entry. Project cards stretch their name link over the card because they
hold their own controls; a client card holds none, so the card is the link.
"""
import pathlib

from design_portal.models import Client, db
from conftest import login_as

CSS = (pathlib.Path(__file__).resolve().parent.parent
       / 'design_portal' / 'static' / 'app.css').read_text()


def a_client(app, **overrides):
    with app.app_context():
        fields = dict(user_id=1, name='Carded', company='Studio Co', email='c@example.com')
        fields.update(overrides)
        client = Client(**fields)
        db.session.add(client)
        db.session.commit()
        return client.id


def test_each_client_is_a_card_that_opens_it(app):
    a, b = a_client(app, name='First'), a_client(app, name='Second')
    body = login_as(app).get('/clients/').get_data(as_text=True)
    for cid in (a, b):
        assert f'href="/clients/{cid}" class="client-card ' in body


def test_client_cards_share_the_project_card_surface():
    assert '.project-card,\n.client-card {' in CSS
    assert '.client-card:focus-visible' in CSS
