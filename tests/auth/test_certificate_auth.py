"""Fork-custom: client-certificate auto-login.

Covers the cert-subject parsing/lookup helpers and the /auth/login
before_request that logs a user in from the X-SSL-Client-Subject header,
including the requirement that cert auth bypasses 2FA.
"""

from flask import url_for

from app.certificate_auth import (
    extract_email_from_cert_subject,
    get_user_from_cert_subject,
)
from app.db import Session
from tests.utils import create_new_user


def test_extract_email_from_cert_subject():
    # explicit emailAddress field wins, lower-cased
    assert (
        extract_email_from_cert_subject("emailAddress=Foo@Bar.com,CN=whatever")
        == "foo@bar.com"
    )
    # CN fallback only when it looks like an email
    assert extract_email_from_cert_subject("CN=foo@bar.com") == "foo@bar.com"
    assert extract_email_from_cert_subject("CN=not-an-email") is None
    assert extract_email_from_cert_subject("O=Org,OU=Unit") is None


def test_get_user_from_cert_subject(flask_client):
    user = create_new_user()
    Session.commit()

    assert get_user_from_cert_subject(f"emailAddress={user.email}").id == user.id
    assert get_user_from_cert_subject("emailAddress=nobody-missing@mailbox.lan") is None
    assert get_user_from_cert_subject("") is None


def test_cert_auto_login_logs_in(flask_client):
    user = create_new_user()
    Session.commit()

    r = flask_client.get(
        url_for("auth.login"),
        headers={"X-SSL-Client-Subject": f"emailAddress={user.email}"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    # landed on the dashboard -> logout link is present only when authenticated
    assert b"/auth/logout" in r.data


def test_cert_auto_login_bypasses_2fa(flask_client):
    user = create_new_user()
    user.enable_otp = True
    user.otp_secret = "base32secret3232"
    Session.commit()

    r = flask_client.get(
        url_for("auth.login"),
        headers={"X-SSL-Client-Subject": f"emailAddress={user.email}"},
        follow_redirects=True,
    )

    assert r.status_code == 200
    # cert auth must skip OTP entirely: a fully-authenticated dashboard shows the
    # logout link; the MFA challenge page would not. Without the bypass this user
    # would be parked on /auth/mfa and this assertion would fail.
    assert b"/auth/logout" in r.data


def test_no_cert_header_does_not_auto_login(flask_client):
    create_new_user()
    Session.commit()

    # no X-SSL-Client-Subject -> normal login page, not authenticated
    r = flask_client.get(url_for("auth.login"))

    assert r.status_code == 200
    assert b"/auth/logout" not in r.data
