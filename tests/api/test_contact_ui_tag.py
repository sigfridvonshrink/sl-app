"""opt-in ui_tag on the contact toggle endpoint.

Lives in a separate file (not the upstream tests/api/test_alias.py) so the
fork's behaviour is locked without editing an upstream test.
"""

from app.db import Session
from app.models import Alias, Contact
from tests.utils import login


def _make_contact(user):
    alias = Alias.create_new_random(user)
    Session.commit()
    contact = Contact.create(
        alias_id=alias.id,
        website_email="contact@example.com",
        reply_email="reply+random@sl.io",
        user_id=alias.user_id,
    )
    Session.commit()
    return contact


def test_toggle_contact_omits_ui_tag_by_default(flask_client):
    """Default response keeps the upstream shape (no ui_tag)."""
    user = login(flask_client)
    contact = _make_contact(user)

    r = flask_client.post(f"/api/contacts/{contact.id}/toggle")

    assert r.status_code == 200
    assert "ui_tag" not in r.json
    assert r.json == {"block_forward": True}


def test_toggle_contact_includes_ui_tag_when_opted_in(flask_client):
    """With ?ui_tag=1 the warning marker is returned for the UI."""
    user = login(flask_client)
    contact = _make_contact(user)

    r = flask_client.post(f"/api/contacts/{contact.id}/toggle?ui_tag=1")

    assert r.status_code == 200
    assert r.json["block_forward"] is True
    assert "ui_tag" in r.json
    assert r.json["ui_tag"] == contact.ui_tag
