from flask import url_for

from app import config
from app.models import EmailChange
from app.utils import canonicalize_email
from tests.utils import login, random_email, create_new_user


def test_setup_done(flask_client):
    config.SKIP_MX_LOOKUP_ON_CHECK = True
    user = create_new_user()
    login(flask_client, user)
    noncanonical_email = f"nonca.{random_email()}"

    r = flask_client.post(
        url_for("dashboard.account_setting"),
        data={
            "form-name": "update-email",
            "email": noncanonical_email,
        },
        follow_redirects=True,
    )

    assert r.status_code == 200
    email_change = EmailChange.get_by(user_id=user.id)
    assert email_change is not None
    assert email_change.new_email == canonicalize_email(noncanonical_email)
    config.SKIP_MX_LOOKUP_ON_CHECK = False


def test_sender_warnings_invalid_decay_does_not_apply_partial_update(flask_client):
    # The sender-warnings form applies atomically on click: an invalid advanced decay
    # field blocks the whole update, so an enable/disable toggle is neither silently
    # dropped by a rollback nor half-applied. A subsequent valid submit applies both.
    from app.db import Session
    from tests.utils import create_new_user

    user = create_new_user()
    login(flask_client, user)
    assert not user.sender_warnings_enabled  # baseline: feature off

    common = {
        "form-name": "sender-warnings",
        "enable_warnings": "on",
        "tier0_days": "1",
        "tier0_count": "2",
        "tier1_marker": "x",
        "tier1_days": "8",
        "tier1_count": "5",
        "floor_marker": ".",
    }

    # invalid: empty tier marker fails validation -> nothing is written
    r = flask_client.post(
        url_for("dashboard.setting"),
        data={**common, "tier0_marker": ""},
        follow_redirects=True,
    )
    assert r.status_code == 200
    Session.refresh(user)
    assert not user.sender_warnings_enabled  # toggle not applied (atomic reject)
    assert user.sender_warning_decay is None

    # valid submit applies the toggle and the decay ladder together
    r = flask_client.post(
        url_for("dashboard.setting"),
        data={**common, "tier0_marker": "!!"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    Session.refresh(user)
    assert user.sender_warnings_enabled
    assert user.sender_warning_decay is not None
