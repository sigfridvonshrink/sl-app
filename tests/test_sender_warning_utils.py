from email.message import Message
from types import SimpleNamespace

import arrow

import pytest

from app.sender_warning_utils import (
    get_warning_marker,
    warning_tier_index,
    should_auto_trust,
    insert_marker_in_subject,
    insert_marker_in_from,
    apply_marker_to_subject,
    apply_marker_to_from,
    sanitize_marker,
    validate_decay_config,
    get_configured_markers,
    decay_count_bound,
    strip_marker_from_subject,
    is_valid_registered_domain,
    DecayConfigError,
    DEFAULT_DECAY,
)


def _contact(hours_ago: float):
    return SimpleNamespace(
        created_at=arrow.utcnow().shift(hours=-hours_ago),
        website_email="sender@example.com",
    )


# get_warning_marker: tag escalates with contact age and email count


def test_get_warning_marker_double_warning_for_new_contact():
    assert get_warning_marker(_contact(hours_ago=1), email_log_count=10) == "◇◇"


def test_get_warning_marker_double_warning_for_low_count():
    assert get_warning_marker(_contact(hours_ago=1000), email_log_count=2) == "◇◇"


def test_get_warning_marker_single_warning_by_age():
    assert get_warning_marker(_contact(hours_ago=100), email_log_count=10) == "◇"


def test_get_warning_marker_single_warning_by_count():
    assert get_warning_marker(_contact(hours_ago=1000), email_log_count=5) == "◇"


def test_get_warning_marker_settled_contact():
    assert get_warning_marker(_contact(hours_ago=1000), email_log_count=10) == "·"


# insert_marker_in_subject: insert at the 3rd / 2nd / 1st non-consecutive space


def test_insert_marker_in_subject_picks_third_space():
    # spaces at indexes 1,3,5,7 -> third (index 5)
    assert insert_marker_in_subject("a b c d e", "T") == "a b c T d e"


def test_insert_marker_in_subject_two_spaces_picks_second():
    assert insert_marker_in_subject("a b c", "T") == "a b T c"


def test_insert_marker_in_subject_one_space_picks_first():
    assert insert_marker_in_subject("a b", "T") == "a T b"


def test_insert_marker_in_subject_no_space_appends():
    assert insert_marker_in_subject("hello", "T") == "helloT"


def test_insert_marker_in_subject_collapses_consecutive_spaces():
    # double space counts as a single non-consecutive index; the second space
    # is preserved after the replaced one
    assert insert_marker_in_subject("a  b", "T") == "a T  b"


def test_insert_marker_in_subject_empty_or_none_returns_tag():
    assert insert_marker_in_subject("", "T") == "T"
    assert insert_marker_in_subject(None, "T") == "T"


# insert_marker_in_from: always insert at the first non-consecutive space


def test_insert_marker_in_from_uses_first_space():
    assert insert_marker_in_from("John Doe Smith", "T") == "John T Doe Smith"


def test_insert_marker_in_from_no_space_appends():
    assert insert_marker_in_from("John", "T") == "JohnT"


def test_insert_marker_in_from_empty_or_none_returns_tag():
    assert insert_marker_in_from("", "T") == "T"
    assert insert_marker_in_from(None, "T") == "T"


# apply_* wrappers: encode the result and keep the email address intact


def test_apply_marker_to_subject_replaces_header():
    msg = Message()
    msg["Subject"] = "a b c d e"
    apply_marker_to_subject(msg, "T", _contact(1), alias=None)
    # only one Subject header remains, and it carries the tag once decoded
    assert len([k for k in msg.keys() if k == "Subject"]) == 1
    from email.header import decode_header, make_header

    decoded = str(make_header(decode_header(msg["Subject"])))
    assert decoded == "a b c T d e"


def test_apply_marker_to_from_preserves_address():
    out = apply_marker_to_from(
        "Jane Roe <jane@example.com>", "T", _contact(1), alias=None
    )
    assert "jane@example.com" in out

    from email.utils import parseaddr
    from email.header import decode_header, make_header

    name, addr = parseaddr(out)
    assert addr == "jane@example.com"
    assert str(make_header(decode_header(name))) == "Jane T Roe"


def _user(decay):
    return SimpleNamespace(sender_warning_decay=decay)


# sanitize_marker: header-injection safety
def test_sanitize_marker_rejects_crlf():
    for bad in ["a\nb", "a\rb", "x\r\ny"]:
        with pytest.raises(DecayConfigError):
            sanitize_marker(bad)


def test_sanitize_marker_rejects_empty_and_too_long():
    with pytest.raises(DecayConfigError):
        sanitize_marker("")
    with pytest.raises(DecayConfigError):
        sanitize_marker("123456789")  # > MAX_MARKER_LEN


def test_sanitize_marker_accepts_emoji():
    assert sanitize_marker("⚠️⚠️") == "⚠️⚠️"


# decay_count_bound: cap for the bounded hot-path email count
def test_decay_count_bound_defaults():
    # default tiers max_count = 2, 5; no auto_trust -> largest threshold + 1
    assert decay_count_bound(_user(None)) == 6


def test_decay_count_bound_includes_auto_trust_min_count():
    cfg = {
        "tiers": [
            {"marker": "a", "max_days": 1, "max_count": 2},
            {"marker": "b", "max_days": 8, "max_count": 5},
        ],
        "floor_marker": "c",
        "auto_trust": {"min_days": 10, "min_count": 40},
    }
    # auto_trust.min_count (40) dominates the tier thresholds
    assert decay_count_bound(_user(cfg)) == 41


def test_decay_count_bound_bad_config_falls_back_to_defaults():
    assert decay_count_bound(_user({"tiers": "garbage"})) == 6


# validate_decay_config
def test_validate_decay_config_defaults_roundtrip():
    cfg = validate_decay_config(dict(DEFAULT_DECAY))
    assert cfg["tiers"][0]["marker"] == "◇◇"
    assert cfg["auto_trust"] is None


def test_validate_decay_config_requires_increasing_tiers():
    with pytest.raises(DecayConfigError):
        validate_decay_config(
            {
                "tiers": [
                    {"marker": "a", "max_days": 200, "max_count": 5},
                    {"marker": "b", "max_days": 24, "max_count": 2},
                ],
                "floor_marker": "c",
                "auto_trust": None,
            }
        )


def test_validate_decay_config_autotrust_must_be_below_last_tier():
    with pytest.raises(DecayConfigError):
        validate_decay_config(
            {
                "tiers": [
                    {"marker": "a", "max_days": 24, "max_count": 2},
                    {"marker": "b", "max_days": 192, "max_count": 5},
                ],
                "floor_marker": "c",
                "auto_trust": {"min_days": 10, "min_count": 1},
            }
        )


def test_validate_decay_config_rejects_crlf_marker():
    with pytest.raises(DecayConfigError):
        validate_decay_config(
            {
                "tiers": [
                    {"marker": "x\r\ny", "max_days": 24, "max_count": 2},
                    {"marker": "b", "max_days": 192, "max_count": 5},
                ],
                "floor_marker": "c",
                "auto_trust": None,
            }
        )


# should_auto_trust: AND polarity, off by default
def test_should_auto_trust_off_by_default():
    assert should_auto_trust(_contact(hours_ago=10000), 100, _user(None)) is False


def test_should_auto_trust_requires_both_axes():
    cfg = {
        "tiers": [
            {"marker": "⚠️⚠️", "max_days": 1, "max_count": 2},
            {"marker": "⚠️", "max_days": 8, "max_count": 5},
        ],
        "floor_marker": "〰️",
        "auto_trust": {"min_days": 10, "min_count": 6},
    }
    user = _user(cfg)
    # hours_ago/24 = age in days
    assert (
        should_auto_trust(_contact(hours_ago=300), 10, user) is True
    )  # 12.5d, 10 msgs
    assert (
        should_auto_trust(_contact(hours_ago=10), 10, user) is False
    )  # 0.4d, too young
    assert should_auto_trust(_contact(hours_ago=300), 3, user) is False  # too few msgs


# custom glyphs flow through tag + strip
def test_custom_glyphs_used_for_tag_and_strip():
    cfg = {
        "tiers": [
            {"marker": "NEW", "max_days": 24, "max_count": 2},
            {"marker": "MID", "max_days": 192, "max_count": 5},
        ],
        "floor_marker": "OLD",
        "auto_trust": None,
    }
    user = _user(cfg)
    assert get_warning_marker(_contact(hours_ago=1), 1, user) == "NEW"
    assert "NEW" in get_configured_markers(user)
    stripped = strip_marker_from_subject("Re: hello NEW world", user)
    assert "NEW" not in stripped


def test_validate_decay_config_rejects_out_of_bounds():
    base_tier = {"marker": "a", "max_days": 1, "max_count": 2}
    # max_days over the 10-year cap
    with pytest.raises(DecayConfigError):
        validate_decay_config(
            {
                "tiers": [
                    base_tier,
                    {"marker": "b", "max_days": 99999, "max_count": 5},
                ],
                "floor_marker": "c",
                "auto_trust": None,
            }
        )
    # max_count over the cap
    with pytest.raises(DecayConfigError):
        validate_decay_config(
            {
                "tiers": [
                    base_tier,
                    {"marker": "b", "max_days": 8, "max_count": 9_999_999},
                ],
                "floor_marker": "c",
                "auto_trust": None,
            }
        )


# strip_marker_from_subject: robust, position-independent round trips
@pytest.mark.parametrize(
    "subject",
    [
        "Hi",  # single word -> appended, no separator
        "Hello world",  # marker lands early (old range would miss)
        "Meeting notes for the team today",
        "A B C D E F G",  # marker lands mid, many tokens
        "x y",  # very short, marker at a tiny index
    ],
)
@pytest.mark.parametrize("prefix", ["", "Re: ", "Fwd: ", "Re: Re: ", "RE: Fw: "])
def test_strip_round_trip_default_markers(subject, prefix):
    user = _user(None)
    tag = get_configured_markers(user)[0]  # longest default marker
    incoming = prefix + insert_marker_in_subject(subject, tag)
    stripped = strip_marker_from_subject(incoming, user)
    assert stripped == prefix + subject
    assert tag not in stripped


def test_strip_round_trip_custom_marker():
    cfg = {
        "tiers": [
            {"marker": "NEW", "max_days": 24, "max_count": 2},
            {"marker": "MID", "max_days": 192, "max_count": 5},
        ],
        "floor_marker": "OLD",
        "auto_trust": None,
    }
    user = _user(cfg)
    for tag in ("NEW", "MID", "OLD"):
        incoming = "Re: " + insert_marker_in_subject("quarterly report", tag)
        assert strip_marker_from_subject(incoming, user) == "Re: quarterly report"


def test_strip_leaves_unrelated_content_untouched():
    user = _user(None)
    tag = get_configured_markers(user)[0]
    # no marker present -> unchanged
    assert strip_marker_from_subject("Re: important meeting", user) == (
        "Re: important meeting"
    )
    # glyph embedded in user content (not the insertion shape) -> unchanged
    embedded = f"warning{tag}sign is fine"
    assert strip_marker_from_subject(embedded, user) == embedded


@pytest.mark.parametrize(
    "domain",
    [
        "good.com",
        "sub.good.com",
        "a-b.co.uk",
        "x1.example.io",
        "9and.com",  # RFC 1123 allows a leading digit
        "xn--mnchen-3ya.de",  # IDN in punycode form
        "münchen.de",  # IDN in unicode form
        "例え.jp",  # non-Latin IDN
        "россия.рф",  # Cyrillic IDN
    ],
)
def test_is_valid_registered_domain_accepts(domain):
    assert is_valid_registered_domain(domain)


@pytest.mark.parametrize(
    "domain",
    [
        "",
        "nodot",
        "a..b.com",
        "-bad.com",
        "bad-.com",
        'a"><script>.com',  # tldextract passes this through unchanged
        "a b.com",
        'evil".com',
        "javascript",
        "x" * 300 + ".com",
        "a_b.com",  # underscore is not legal in a hostname
    ],
)
def test_is_valid_registered_domain_rejects(domain):
    assert not is_valid_registered_domain(domain)


# warning_tier_index: numeric state that drives both the email glyph and the
# dashboard label (0 = tiers[0], 1 = tiers[1], 2 = floor for the default 2-tier ladder)
def test_warning_tier_index_new_contact():
    assert warning_tier_index(_contact(hours_ago=1), 10) == 0


def test_warning_tier_index_low_count():
    assert warning_tier_index(_contact(hours_ago=1000), 2) == 0


def test_warning_tier_index_middle():
    assert warning_tier_index(_contact(hours_ago=100), 10) == 1


def test_warning_tier_index_floor():
    assert warning_tier_index(_contact(hours_ago=1000), 10) == 2


def test_email_glyph_matches_tier_index():
    # guards the "email output stays byte-identical" contract: the injected glyph
    # is exactly the configured marker for the computed tier index
    glyphs = [t["marker"] for t in DEFAULT_DECAY["tiers"]] + [
        DEFAULT_DECAY["floor_marker"]
    ]
    for hours, count in [(1, 10), (1000, 2), (100, 10), (1000, 10)]:
        c = _contact(hours_ago=hours)
        assert get_warning_marker(c, count) == glyphs[warning_tier_index(c, count)]
