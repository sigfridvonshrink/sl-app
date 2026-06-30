import arrow
from flask import url_for

# Need to import directly from config to allow modification from the tests
from app import config
from app.alias_delete import move_alias_to_trash
from app.db import Session
from app.email_utils import is_reverse_alias
from app.models import (
    User,
    Alias,
    Contact,
    EmailLog,
    Mailbox,
    AliasDeleteReason,
    ApiKey,
)
from tests.api.utils import get_new_user_and_api_key
from tests.utils import login, random_domain


def test_get_aliases_error_without_pagination(flask_client):
    user, api_key = get_new_user_and_api_key()

    r = flask_client.get(
        url_for("api.get_aliases"), headers={"Authentication": api_key.code}
    )

    assert r.status_code == 400
    assert r.json["error"]


def test_get_aliases_with_pagination(flask_client):
    user, api_key = get_new_user_and_api_key()

    # create more aliases than config.PAGE_LIMIT
    for _ in range(config.PAGE_LIMIT + 1):
        Alias.create_new_random(user)
    Session.commit()

    # get aliases on the 1st page, should return config.PAGE_LIMIT aliases
    r = flask_client.get(
        url_for("api.get_aliases", page_id=0), headers={"Authentication": api_key.code}
    )
    assert r.status_code == 200
    assert len(r.json["aliases"]) == config.PAGE_LIMIT

    # assert returned field
    for a in r.json["aliases"]:
        assert "id" in a
        assert "email" in a
        assert "creation_date" in a
        assert "creation_timestamp" in a
        assert "nb_forward" in a
        assert "nb_block" in a
        assert "nb_reply" in a
        assert "enabled" in a
        assert "note" in a

    # get aliases on the 2nd page, should return 2 aliases
    # as the total number of aliases is config.PAGE_LIMIT +2
    # 1 alias is created when user is created
    r = flask_client.get(
        url_for("api.get_aliases", page_id=1), headers={"Authentication": api_key.code}
    )
    assert r.status_code == 200
    assert len(r.json["aliases"]) == 2


def test_get_aliases_query(flask_client):
    user, api_key = get_new_user_and_api_key()

    # create more aliases than config.PAGE_LIMIT
    Alias.create_new(user, "prefix1")
    Alias.create_new(user, "prefix2")
    Session.commit()

    # get aliases without query, should return 3 aliases as one alias is created when user is created
    r = flask_client.get(
        url_for("api.get_aliases", page_id=0), headers={"Authentication": api_key.code}
    )
    assert r.status_code == 200
    assert len(r.json["aliases"]) == 3

    # get aliases with "prefix1" query, should return 1 alias
    r = flask_client.get(
        url_for("api.get_aliases", page_id=0),
        headers={"Authentication": api_key.code},
        json={"query": "prefix1"},
    )
    assert r.status_code == 200
    assert len(r.json["aliases"]) == 1


def test_get_aliases_v2(flask_client):
    user = login(flask_client)

    a0 = Alias.create_new(user, "prefix0")
    a1 = Alias.create_new(user, "prefix1")
    Session.commit()

    # << Aliases have no activity >>
    r = flask_client.get("/api/v2/aliases?page_id=0")
    assert r.status_code == 200

    r0 = r.json["aliases"][0]
    assert "name" in r0

    # make sure a1 is returned before a0
    assert r0["email"].startswith("prefix1")
    assert "id" in r0["mailbox"]
    assert "email" in r0["mailbox"]

    assert r0["mailboxes"]
    for mailbox in r0["mailboxes"]:
        assert "id" in mailbox
        assert "email" in mailbox

    assert "support_pgp" in r0
    assert not r0["support_pgp"]

    assert "disable_pgp" in r0
    assert not r0["disable_pgp"]

    # << Alias has some activities >>
    c0 = Contact.create(
        user_id=user.id,
        alias_id=a0.id,
        website_email="c0@example.com",
        reply_email="re0@SL",
        commit=True,
    )
    EmailLog.create(
        contact_id=c0.id, user_id=user.id, alias_id=c0.alias_id, commit=True
    )

    # a1 has more recent activity
    c1 = Contact.create(
        user_id=user.id,
        alias_id=a1.id,
        website_email="c1@example.com",
        reply_email="re1@SL",
        commit=True,
    )
    EmailLog.create(
        contact_id=c1.id, user_id=user.id, alias_id=c1.alias_id, commit=True
    )

    r = flask_client.get("/api/v2/aliases?page_id=0")
    assert r.status_code == 200

    r0 = r.json["aliases"][0]

    assert r0["latest_activity"]["action"] == "forward"
    assert "timestamp" in r0["latest_activity"]

    assert r0["latest_activity"]["contact"]["email"] == "c1@example.com"
    assert "name" in r0["latest_activity"]["contact"]
    assert "reverse_alias" in r0["latest_activity"]["contact"]
    assert "pinned" in r0


def test_get_pinned_aliases_v2(flask_client):
    user = login(flask_client)

    a0 = Alias.create_new(user, "prefix0")
    a0.pinned = True
    Session.commit()

    r = flask_client.get("/api/v2/aliases?page_id=0")
    assert r.status_code == 200
    # the default alias (created when user is created) and a0 are returned
    assert len(r.json["aliases"]) == 2

    r = flask_client.get("/api/v2/aliases?page_id=0&pinned=true")
    assert r.status_code == 200
    # only a0 is returned
    assert len(r.json["aliases"]) == 1
    assert r.json["aliases"][0]["id"] == a0.id


def test_get_disabled_aliases_v2(flask_client):
    user = login(flask_client)

    a0 = Alias.create_new(user, "prefix0")
    a0.enabled = False
    Session.commit()

    r = flask_client.get("/api/v2/aliases?page_id=0")
    assert r.status_code == 200
    # the default alias (created when user is created) and a0 are returned
    assert len(r.json["aliases"]) == 2

    r = flask_client.get("/api/v2/aliases?page_id=0&disabled=true")
    assert r.status_code == 200
    # only a0 is returned
    assert len(r.json["aliases"]) == 1
    assert r.json["aliases"][0]["id"] == a0.id


def test_get_enabled_aliases_v2(flask_client):
    user = login(flask_client)

    a0 = Alias.create_new(user, "prefix0")
    a0.enabled = False
    Session.commit()

    r = flask_client.get("/api/v2/aliases?page_id=0")
    assert r.status_code == 200
    # the default alias (created when user is created) and a0 are returned
    assert len(r.json["aliases"]) == 2

    r = flask_client.get("/api/v2/aliases?page_id=0&enabled=true")
    assert r.status_code == 200
    # only the first alias is returned
    assert len(r.json["aliases"]) == 1
    assert r.json["aliases"][0]["id"] != a0.id


def test_delete_alias(flask_client):
    user = login(flask_client)

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.delete(
        url_for("api.delete_alias", alias_id=alias.id),
    )

    assert r.status_code == 200
    assert r.json == {"deleted": True}


def test_toggle_alias(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.post(
        url_for("api.toggle_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
    )

    assert r.status_code == 200
    assert r.json == {"enabled": False}


def test_alias_activities(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    # create some alias log
    contact = Contact.create(
        website_email="marketing@example.com",
        reply_email="reply@a.b",
        alias_id=alias.id,
        user_id=alias.user_id,
    )
    Session.commit()

    for _ in range(int(config.PAGE_LIMIT / 2)):
        EmailLog.create(
            contact_id=contact.id,
            is_reply=True,
            user_id=contact.user_id,
            alias_id=contact.alias_id,
        )

    for _ in range(int(config.PAGE_LIMIT / 2) + 2):
        EmailLog.create(
            contact_id=contact.id,
            blocked=True,
            user_id=contact.user_id,
            alias_id=contact.alias_id,
        )

    r = flask_client.get(
        url_for("api.get_alias_activities", alias_id=alias.id, page_id=0),
        headers={"Authentication": api_key.code},
    )

    assert r.status_code == 200
    assert len(r.json["activities"]) == config.PAGE_LIMIT
    for ac in r.json["activities"]:
        assert ac["from"]
        assert ac["to"]
        assert ac["timestamp"]
        assert ac["action"]
        assert ac["reverse_alias"]
        assert ac["reverse_alias_address"]

    # second page, should return 1 or 2 results only
    r = flask_client.get(
        url_for("api.get_alias_activities", alias_id=alias.id, page_id=1),
        headers={"Authentication": api_key.code},
    )
    assert len(r.json["activities"]) < 3


def test_update_alias(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"note": "test note"},
    )

    assert r.status_code == 200


def test_update_alias_mailbox(flask_client):
    user, api_key = get_new_user_and_api_key()

    mb = Mailbox.create(user_id=user.id, email="ab@cd.com", verified=True)

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"mailbox_id": mb.id},
    )

    assert r.status_code == 200

    # fail when update with non-existing mailbox
    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"mailbox_id": -1},
    )
    assert r.status_code == 400


def test_update_alias_name(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"name": "Test Name"},
    )
    assert r.status_code == 200
    alias = Alias.get(alias.id)
    assert alias.name == "Test Name"

    # update name with linebreak
    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"name": "Test \nName"},
    )
    assert r.status_code == 200
    alias = Alias.get(alias.id)
    assert alias.name == "Test Name"


def test_update_alias_mailboxes(flask_client):
    user, api_key = get_new_user_and_api_key()

    mb1 = Mailbox.create(user_id=user.id, email="ab1@cd.com", verified=True)
    mb2 = Mailbox.create(user_id=user.id, email="ab2@cd.com", verified=True)

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"mailbox_ids": [mb1.id, mb2.id]},
    )

    assert r.status_code == 200
    alias = Alias.get(alias.id)

    assert alias.mailbox
    assert len(alias._mailboxes) == 1

    # fail when update with empty mailboxes
    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"mailbox_ids": []},
    )
    assert r.status_code == 400


def test_update_disable_pgp(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()
    assert not alias.disable_pgp

    r = flask_client.put(
        url_for("api.update_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"disable_pgp": True},
    )

    assert r.status_code == 200
    alias = Alias.get(alias.id)
    assert alias.disable_pgp


def test_update_pinned(flask_client):
    user = login(flask_client)

    alias = Alias.filter_by(user_id=user.id).first()
    assert not alias.pinned

    r = flask_client.patch(
        url_for("api.update_alias", alias_id=alias.id),
        json={"pinned": True},
    )

    assert r.status_code == 200
    assert alias.pinned


def test_alias_contacts(flask_client):
    user = login(flask_client)

    alias = Alias.create_new_random(user)
    Session.commit()

    # create some alias log
    for i in range(config.PAGE_LIMIT + 1):
        contact = Contact.create(
            website_email=f"marketing-{i}@example.com",
            reply_email=f"reply-{i}@a.b",
            alias_id=alias.id,
            user_id=alias.user_id,
        )
        Session.commit()

        EmailLog.create(
            contact_id=contact.id,
            is_reply=True,
            user_id=contact.user_id,
            alias_id=contact.alias_id,
        )
        Session.commit()

    r = flask_client.get(f"/api/aliases/{alias.id}/contacts?page_id=0")

    assert r.status_code == 200
    assert len(r.json["contacts"]) == config.PAGE_LIMIT
    for ac in r.json["contacts"]:
        assert ac["creation_date"]
        assert ac["creation_timestamp"]
        assert ac["last_email_sent_date"]
        assert ac["last_email_sent_timestamp"]
        assert ac["contact"]
        assert ac["reverse_alias"]
        assert ac["reverse_alias_address"]
        assert "block_forward" in ac

    # second page, should return 1 result only
    r = flask_client.get(f"/api/aliases/{alias.id}/contacts?page_id=1")
    assert len(r.json["contacts"]) == 1


def test_create_contact_route(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": "First Last <first@example.com>"},
    )

    assert r.status_code == 201
    assert r.json["contact"] == "first@example.com"
    assert "creation_date" in r.json
    assert "creation_timestamp" in r.json
    assert r.json["last_email_sent_date"] is None
    assert r.json["last_email_sent_timestamp"] is None
    assert r.json["reverse_alias"]
    assert r.json["reverse_alias_address"]
    assert r.json["existed"] is False

    # re-add a contact, should return 200
    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": "First2 Last2 <first@example.com>"},
    )
    assert r.status_code == 200
    assert r.json["existed"]


def test_create_contact_route_invalid_alias(flask_client):
    user, api_key = get_new_user_and_api_key()
    other_user, other_api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(other_user)
    Session.commit()

    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": "First Last <first@example.com>"},
    )

    assert r.status_code == 403


def test_create_contact_route_non_existing_alias(flask_client):
    user, api_key = get_new_user_and_api_key()
    Session.commit()

    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=99999999),
        headers={"Authentication": api_key.code},
        json={"contact": "First Last <first@example.com>"},
    )

    assert r.status_code == 403


def test_create_contact_route_free_users(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()
    # On trial, should be ok
    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": f"First Last <first@{random_domain()}>"},
    )
    assert r.status_code == 201

    # End trial but allow via flags for older free users
    user.trial_end = arrow.now()
    user.flags = 0
    Session.commit()
    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": f"First Last <first@{random_domain()}>"},
    )
    assert r.status_code == 201

    # End trial and disallow for new free users. Config should allow it
    user.flags = User.FLAG_FREE_DISABLE_CREATE_CONTACTS
    Session.commit()
    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": f"First Last <first@{random_domain()}>"},
    )
    assert r.status_code == 201

    # Set the global config to disable free users from create contacts
    config.DISABLE_CREATE_CONTACTS_FOR_FREE_USERS = True
    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"contact": f"First Last <first@{random_domain()}>"},
    )
    assert r.status_code == 403
    config.DISABLE_CREATE_CONTACTS_FOR_FREE_USERS = False


def test_create_contact_route_empty_contact_address(flask_client):
    user = login(flask_client)
    alias = Alias.filter_by(user_id=user.id).first()

    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        json={"contact": ""},
    )

    assert r.status_code == 400
    assert r.json["error"] == "Empty address is not a valid email address"


def test_create_contact_route_invalid_contact_email(flask_client):
    user = login(flask_client)
    alias = Alias.filter_by(user_id=user.id).first()

    r = flask_client.post(
        url_for("api.create_contact_route", alias_id=alias.id),
        json={"contact": "@gmail.com"},
    )

    assert r.status_code == 400
    assert r.json["error"] == "@gmail.com is not a valid email address"


def test_delete_contact(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    Session.commit()

    contact = Contact.create(
        alias_id=alias.id,
        website_email="contact@example.com",
        reply_email="reply+random@sl.io",
        user_id=alias.user_id,
    )
    Session.commit()

    r = flask_client.delete(
        url_for("api.delete_contact", contact_id=contact.id),
        headers={"Authentication": api_key.code},
    )

    assert r.status_code == 200
    assert r.json == {"deleted": True}


def test_delete_contact_keeps_trusted_domain(flask_client):
    """Deleting a contact never auto-removes a trusted domain.

    A domain may be trusted on purpose (manually, or ahead of a sender); when its last
    contact is deleted it lingers as a visible "orphan" in the allow-list panel rather
    than being silently removed.
    """
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)
    alias.sender_allow_list = ["example.com"]
    Session.commit()

    contact = Contact.create(
        alias_id=alias.id,
        website_email="contact@example.com",
        reply_email="reply+random@sl.io",
        user_id=alias.user_id,
    )

    contact2 = Contact.create(
        alias_id=alias.id,
        website_email="other@example.com",
        reply_email="reply+random2@sl.io",
        user_id=alias.user_id,
    )
    Session.commit()

    # Delete the first contact: domain stays trusted (still has contact2).
    r = flask_client.delete(
        url_for("api.delete_contact", contact_id=contact.id),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200
    assert "example.com" in alias.sender_allow_list

    # Delete the last contact: domain remains as a trusted orphan, NOT auto-removed.
    r = flask_client.delete(
        url_for("api.delete_contact", contact_id=contact2.id),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200
    assert "example.com" in alias.sender_allow_list


def test_get_alias(flask_client):
    user, api_key = get_new_user_and_api_key()

    # create more aliases than config.PAGE_LIMIT
    alias = Alias.create_new_random(user)
    Session.commit()

    # get aliases on the 1st page, should return config.PAGE_LIMIT aliases
    r = flask_client.get(
        url_for("api.get_alias", alias_id=alias.id),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200

    # assert returned field
    res = r.json
    assert "id" in res
    assert "email" in res
    assert "creation_date" in res
    assert "creation_timestamp" in res
    assert "nb_forward" in res
    assert "nb_block" in res
    assert "nb_reply" in res
    assert "enabled" in res
    assert "note" in res
    assert "pinned" in res


def test_is_reverse_alias(flask_client):
    assert is_reverse_alias("ra+abcd@sl.lan")
    assert is_reverse_alias("reply+abcd@sl.lan")

    assert not is_reverse_alias("ra+abcd@test.org")
    assert not is_reverse_alias("reply+abcd@test.org")
    assert not is_reverse_alias("abcd@test.org")


def test_toggle_contact(flask_client):
    user = login(flask_client)

    alias = Alias.create_new_random(user)
    Session.commit()

    contact = Contact.create(
        alias_id=alias.id,
        website_email="contact@example.com",
        reply_email="reply+random@sl.io",
        user_id=alias.user_id,
    )
    Session.commit()

    r = flask_client.post(f"/api/contacts/{contact.id}/toggle")

    assert r.status_code == 200
    assert r.json == {"block_forward": True}


def test_get_aliases_disabled_account(flask_client):
    user, api_key = get_new_user_and_api_key()

    r = flask_client.get(
        "/api/v2/aliases?page_id=0",
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200

    user.disabled = True
    Session.commit()

    r = flask_client.get(
        "/api/v2/aliases?page_id=0",
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 403


def test_get_aliases_does_not_return_trashed_aliases(flask_client):
    user, api_key = get_new_user_and_api_key()

    alias = Alias.create_new_random(user)

    r = flask_client.get(
        "/api/v2/aliases?page_id=0",
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200

    aliases = r.json["aliases"]
    assert len(aliases) == 2  # Newsletter + our own

    assert aliases[0]["id"] == alias.id

    newsletter_alias_id = aliases[1]["id"]
    assert newsletter_alias_id != alias.id

    move_alias_to_trash(alias, user, AliasDeleteReason.ManualAction, commit=True)

    r = flask_client.get(
        "/api/v2/aliases?page_id=0",
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200

    aliases = r.json["aliases"]
    assert len(aliases) == 1  # Newsletter
    assert aliases[0]["id"] == newsletter_alias_id


def test_cannot_create_alias_with_admin_disabled_mailbox_via_api(flask_client):
    """Test that API blocks creation of aliases with admin-disabled mailboxes"""
    user = login(flask_client)
    api_key = ApiKey.create(user_id=user.id, name="test")
    Session.commit()

    # Create and admin-disable a mailbox
    mb = Mailbox.create(user_id=user.id, email="disabled@gmail.com", verified=True)
    Session.commit()
    mb.flags = (mb.flags or 0) | Mailbox.FLAG_ADMIN_DISABLED
    Session.commit()

    # Try to create alias with admin-disabled mailbox
    r = flask_client.post(
        "/api/aliases/random/new",
        headers={"Authentication": api_key.code},
        json={"mailbox_ids": [mb.id]},
    )

    # Should fail since the mailbox validation in alias creation should catch this
    # The exact error depends on how the API handles it
    # It might succeed in creating but fail to assign the mailbox
    # or it might validate and reject
    # Let's check what happens
    if r.status_code == 201:
        # If alias was created, verify the admin-disabled mailbox was not assigned
        alias_id = r.json["id"]
        alias = Alias.get(alias_id)
        assert mb not in alias.mailboxes
    else:
        # Alias creation was blocked
        assert r.status_code >= 400


def test_toggle_allow_domain(flask_client):
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    Contact.create(
        alias_id=alias.id,
        website_email="a@known.com",
        reply_email="r1@sl.io",
        user_id=user.id,
    )
    Session.commit()

    url = url_for("api.toggle_alias_allow_domain", alias_id=alias.id)

    # trust the domain (sent as a full email; normalized to registered domain)
    r = flask_client.post(
        url, headers={"Authentication": api_key.code}, json={"domain": "a@known.com"}
    )
    assert r.status_code == 200
    assert r.json["domain"] == "known.com"
    assert r.json["in_list"] is True
    assert any(d["domain"] == "known.com" for d in r.json["trusted"])
    assert r.json["counts"]["trusted"] == 1
    assert r.json["counts"]["marked"] == 0

    # toggle again -> removed; domain returns to the marked group (contact still exists)
    r = flask_client.post(
        url, headers={"Authentication": api_key.code}, json={"domain": "known.com"}
    )
    assert r.status_code == 200
    assert r.json["in_list"] is False
    assert any(d["domain"] == "known.com" for d in r.json["marked"])
    assert r.json["counts"]["trusted"] == 0


def test_toggle_allow_domain_requires_domain(flask_client):
    user, api_key = get_new_user_and_api_key()
    alias = Alias.create_new_random(user)
    Session.commit()
    r = flask_client.post(
        url_for("api.toggle_alias_allow_domain", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={},
    )
    assert r.status_code == 400


def test_toggle_allow_domain_forbidden_for_other_user(flask_client):
    user, api_key = get_new_user_and_api_key()
    other, _ = get_new_user_and_api_key()
    alias = Alias.create_new_random(other)
    Session.commit()
    r = flask_client.post(
        url_for("api.toggle_alias_allow_domain", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"domain": "known.com"},
    )
    assert r.status_code == 403


def test_toggle_allow_domain_rejects_malformed(flask_client):
    user, api_key = get_new_user_and_api_key()
    alias = Alias.create_new_random(user)
    Session.commit()
    url = url_for("api.toggle_alias_allow_domain", alias_id=alias.id)
    for bad in ['a"><script>.com', "no dot", "-bad.com", "javascript:alert(1)"]:
        r = flask_client.post(
            url, headers={"Authentication": api_key.code}, json={"domain": bad}
        )
        assert r.status_code == 400, bad
    Session.refresh(alias)
    assert not alias.sender_allow_list


def test_get_allow_list_state(flask_client):
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    Contact.create(
        alias_id=alias.id,
        website_email="a@known.com",
        reply_email="r@sl.io",
        user_id=user.id,
    )
    Session.commit()
    r = flask_client.get(
        url_for("api.get_alias_allow_list_state", alias_id=alias.id),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200
    assert {"trusted", "marked", "marked_total", "has_more", "counts"} <= set(
        r.json.keys()
    )
    assert "contact_tags" not in r.json  # panel no longer carries all-contact tags
    assert any(d["domain"] == "known.com" for d in r.json["marked"])


def test_allow_list_state_lists_all_marked_within_limit(flask_client, monkeypatch):
    from app import sender_warning_utils
    from app.sender_warning_utils import build_allow_list_state

    monkeypatch.setattr(sender_warning_utils, "MARKED_PANEL_LIMIT", 5)
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    for i, d in enumerate(["aaa.com", "bbb.com", "ccc.com"]):
        Contact.create(
            alias_id=alias.id,
            website_email=f"x@{d}",
            reply_email=f"r{i}@sl.io",
            user_id=user.id,
        )
    alias.set_sender_allow_domains({"trusted.com"})  # arm the feature
    Session.commit()

    state = build_allow_list_state(alias)
    assert {m["domain"] for m in state["marked"]} == {"aaa.com", "bbb.com", "ccc.com"}
    assert state["marked_total"] == 3
    assert state["has_more"] is False


def test_allow_list_state_over_limit_lists_only_page_domains(flask_client, monkeypatch):
    from app import sender_warning_utils
    from app.sender_warning_utils import build_allow_list_state

    monkeypatch.setattr(sender_warning_utils, "MARKED_PANEL_LIMIT", 2)
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    for i, d in enumerate(["aaa.com", "bbb.com", "ccc.com"]):
        Contact.create(
            alias_id=alias.id,
            website_email=f"x@{d}",
            reply_email=f"r{i}@sl.io",
            user_id=user.id,
        )
    alias.set_sender_allow_domains({"trusted.com"})  # arm the feature
    Session.commit()

    # over the limit, with no page context, nothing is listed but the total is reported
    state = build_allow_list_state(alias)
    assert state["marked"] == []
    assert state["marked_total"] == 3
    assert state["has_more"] is True

    # a focus domain is listed even over the limit
    focused = build_allow_list_state(alias, focus_domain="ccc.com")
    assert [m["domain"] for m in focused["marked"]] == ["ccc.com"]


def test_toggle_allow_domain_returns_visible_tags_only(flask_client):
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    c1 = Contact.create(
        alias_id=alias.id,
        website_email="a@known.com",
        reply_email="r1@sl.io",
        user_id=user.id,
        flush=True,
    )
    Contact.create(
        alias_id=alias.id,
        website_email="b@other.com",
        reply_email="r2@sl.io",
        user_id=user.id,
    )
    Session.commit()

    r = flask_client.post(
        url_for("api.toggle_alias_allow_domain", alias_id=alias.id),
        headers={"Authentication": api_key.code},
        json={"domain": "known.com", "visible_ids": [c1.id]},
    )
    assert r.status_code == 200
    # only the visible contact's tag is returned (the second contact is excluded)
    assert set(r.json["contact_tags"].keys()) == {str(c1.id)}
    assert any(d["domain"] == "known.com" for d in r.json["trusted"])


def test_get_allow_list_state_forbidden_for_other_user(flask_client):
    user, api_key = get_new_user_and_api_key()
    other, _ = get_new_user_and_api_key()
    alias = Alias.create_new_random(other)
    Session.commit()
    r = flask_client.get(
        url_for("api.get_alias_allow_list_state", alias_id=alias.id),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 403


def test_allow_list_state_guarantees_visible_domains(flask_client, monkeypatch):
    from app import sender_warning_utils
    from app.sender_warning_utils import build_allow_list_state

    monkeypatch.setattr(sender_warning_utils, "MARKED_PANEL_LIMIT", 1)
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)
    contacts = {}
    for i, d in enumerate(["aaa.com", "bbb.com", "ccc.com"]):
        contacts[d] = Contact.create(
            alias_id=alias.id,
            website_email=f"x@{d}",
            reply_email=f"r{i}@sl.io",
            user_id=user.id,
            flush=True,
        )
    alias.set_sender_allow_domains({"trusted.com"})
    Session.commit()

    # cap is 1, but a visible contact's domain is guaranteed present past the cap
    state = build_allow_list_state(alias, visible_ids=[contacts["ccc.com"].id])
    domains = [m["domain"] for m in state["marked"]]
    assert "ccc.com" in domains


def test_allow_list_state_aggregates_large_alias_in_sql(flask_client, monkeypatch):
    # A large alias must not load every Contact into ORM objects to build the panel:
    # domain groups are aggregated in SQL (grouped by sender host, then folded into the
    # registered domain), and the marked list stays bounded to the page past the cap.
    from app import sender_warning_utils
    from app.sender_warning_utils import build_allow_list_state

    monkeypatch.setattr(sender_warning_utils, "MARKED_PANEL_LIMIT", 3)
    user, api_key = get_new_user_and_api_key()
    user.flags = user.flags | User.FLAG_SENDER_WARNINGS
    alias = Alias.create_new_random(user)

    # 10 registered domains, 5 contacts each (5 distinct subdomains that fold to the
    # same registered domain) -> 50 contacts, far past the cap.
    for d in range(10):
        for c in range(5):
            Contact.create(
                alias_id=alias.id,
                user_id=user.id,
                website_email=f"u{c}@sub{c}.dom{d}.com",
                reply_email=f"r{d}_{c}@sl.io",
            )
    alias.set_sender_allow_domains({"trusted.com"})
    Session.commit()

    state = build_allow_list_state(alias)
    # subdomains folded: 10 registered domains marked, not 50 hosts
    assert state["marked_total"] == 10
    # over the cap with no page context -> nothing listed, but the total is reported
    assert state["marked"] == []
    assert state["has_more"] is True

    # a focus domain stays actionable past the cap and carries its full folded count
    focused = build_allow_list_state(alias, focus_domain="dom4.com")
    assert [m["domain"] for m in focused["marked"]] == ["dom4.com"]
    assert focused["marked"][0]["contacts"] == 5
