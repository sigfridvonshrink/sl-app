from email.message import Message
from types import SimpleNamespace

import arrow

from app.whitelist_utils import (
    get_whitelist_tag,
    insert_tag_subject,
    insert_tag_from,
    apply_whitelist_tag_to_subject,
    apply_whitelist_tag_to_from,
)


def _contact(hours_ago: float):
    return SimpleNamespace(
        created_at=arrow.utcnow().shift(hours=-hours_ago),
        website_email="sender@example.com",
    )


# get_whitelist_tag: tag escalates with contact age and email count


def test_get_whitelist_tag_double_warning_for_new_contact():
    assert get_whitelist_tag(_contact(hours_ago=1), email_log_count=10) == "⚠️⚠️"


def test_get_whitelist_tag_double_warning_for_low_count():
    assert get_whitelist_tag(_contact(hours_ago=1000), email_log_count=2) == "⚠️⚠️"


def test_get_whitelist_tag_single_warning_by_age():
    assert get_whitelist_tag(_contact(hours_ago=100), email_log_count=10) == "⚠️"


def test_get_whitelist_tag_single_warning_by_count():
    assert get_whitelist_tag(_contact(hours_ago=1000), email_log_count=5) == "⚠️"


def test_get_whitelist_tag_settled_contact():
    assert get_whitelist_tag(_contact(hours_ago=1000), email_log_count=10) == "〰️"


# insert_tag_subject: insert at the 3rd / 2nd / 1st non-consecutive space


def test_insert_tag_subject_picks_third_space():
    # spaces at indexes 1,3,5,7 -> third (index 5)
    assert insert_tag_subject("a b c d e", "T") == "a b c T d e"


def test_insert_tag_subject_two_spaces_picks_second():
    assert insert_tag_subject("a b c", "T") == "a b T c"


def test_insert_tag_subject_one_space_picks_first():
    assert insert_tag_subject("a b", "T") == "a T b"


def test_insert_tag_subject_no_space_appends():
    assert insert_tag_subject("hello", "T") == "helloT"


def test_insert_tag_subject_collapses_consecutive_spaces():
    # double space counts as a single non-consecutive index; the second space
    # is preserved after the replaced one
    assert insert_tag_subject("a  b", "T") == "a T  b"


def test_insert_tag_subject_empty_or_none_returns_tag():
    assert insert_tag_subject("", "T") == "T"
    assert insert_tag_subject(None, "T") == "T"


# insert_tag_from: always insert at the first non-consecutive space


def test_insert_tag_from_uses_first_space():
    assert insert_tag_from("John Doe Smith", "T") == "John T Doe Smith"


def test_insert_tag_from_no_space_appends():
    assert insert_tag_from("John", "T") == "JohnT"


def test_insert_tag_from_empty_or_none_returns_tag():
    assert insert_tag_from("", "T") == "T"
    assert insert_tag_from(None, "T") == "T"


# apply_* wrappers: encode the result and keep the email address intact


def test_apply_whitelist_tag_to_subject_replaces_header():
    msg = Message()
    msg["Subject"] = "a b c d e"
    apply_whitelist_tag_to_subject(msg, "T", _contact(1), alias=None)
    # only one Subject header remains, and it carries the tag once decoded
    assert len([k for k in msg.keys() if k == "Subject"]) == 1
    from email.header import decode_header, make_header

    decoded = str(make_header(decode_header(msg["Subject"])))
    assert decoded == "a b c T d e"


def test_apply_whitelist_tag_to_from_preserves_address():
    out = apply_whitelist_tag_to_from(
        "Jane Roe <jane@example.com>", "T", _contact(1), alias=None
    )
    assert "jane@example.com" in out

    from email.utils import parseaddr
    from email.header import decode_header, make_header

    name, addr = parseaddr(out)
    assert addr == "jane@example.com"
    assert str(make_header(decode_header(name))) == "Jane T Roe"
