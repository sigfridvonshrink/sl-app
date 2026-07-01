"""Unexpected-sender warning logic (soft sender allow-list).

Single source of truth for the feature:

- decay configuration (per-user tiers / glyphs / auto-trust terminus) with built-in
  defaults, plus validation that is header-injection-safe,
- computing the marker a non-trusted sender's mail/contact should carry,
- the lazy auto-trust check,
- inserting the marker into a forwarded mail's Subject / From header,
- building the per-alias allow-list panel state for the dashboard + API.

The thresholds/glyphs live here once; the email handler, the contact-manager view,
and the toggle endpoint all call into this module so there is no duplicated decay
logic in templates or JavaScript.
"""

import copy
import re
from collections import Counter
from email.header import Header
from email.message import Message
from email.utils import formataddr

import arrow
from sqlalchemy import func

from app import config
from app.db import Session
from app.log import LOG
from app.models import Contact, Alias, EmailLog
from app.email_utils import (
    add_or_replace_header,
    get_header_unicode,
    parse_full_address,
)

# Dashboard-only marker for a trusted sender (never inserted into mail).
TRUSTED_MARKER = "✅"

# Marker strings are inserted into outgoing mail headers, so they are length- and
# content-restricted (see sanitize_marker). Bounds live in config.
MAX_MARKER_LEN = config.SENDER_WARNING_MARKER_MAX_LEN
MAX_DECAY_DAYS = config.SENDER_WARNING_MAX_DECAY_DAYS
MAX_DECAY_COUNT = config.SENDER_WARNING_MAX_DECAY_COUNT

# Built-in decay ladder, used when the user has no custom config.
DEFAULT_DECAY = {
    "tiers": [
        {"marker": "◇◇", "max_days": 1, "max_count": 2},
        {"marker": "◇", "max_days": 8, "max_count": 5},
    ],
    "floor_marker": "·",
    "auto_trust": None,
}


class DecayConfigError(ValueError):
    """Raised when a user-supplied decay config fails validation."""


def sanitize_marker(value) -> str:
    """Validate a user-supplied marker glyph that will land in a mail header.

    Rejects CR/LF and other control characters (header-injection vector), enforces a
    length cap, and requires a non-empty result (an empty marker would silently break
    the "no marker => trusted" invariant). Returns the marker unchanged on success.
    """
    if not isinstance(value, str):
        raise DecayConfigError("marker must be a string")
    if value == "":
        raise DecayConfigError("marker cannot be empty")
    if len(value) > MAX_MARKER_LEN:
        raise DecayConfigError(f"marker cannot exceed {MAX_MARKER_LEN} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise DecayConfigError("marker cannot contain control characters")
    # must be encodable as an email header
    try:
        Header(value, "utf-8").encode()
    except Exception:
        raise DecayConfigError("marker is not a valid email-header value")
    return value


def _validate_threshold(value, name: str, allow_zero: bool, max_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecayConfigError(f"{name} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        raise DecayConfigError(f"{name} must be a positive integer")
    if value > max_value:
        raise DecayConfigError(f"{name} must be at most {max_value}")
    return value


def validate_decay_config(data: dict) -> dict:
    """Validate and normalize a decay config dict. Raises DecayConfigError on bad input.

    Shape:
        {"tiers": [{"marker", "max_days", "max_count"}, ...],
         "floor_marker": str,
         "auto_trust": None | {"min_days", "min_count"}}
    """
    if not isinstance(data, dict):
        raise DecayConfigError("config must be an object")

    raw_tiers = data.get("tiers")
    if not isinstance(raw_tiers, list) or not raw_tiers:
        raise DecayConfigError("tiers must be a non-empty list")

    tiers = []
    prev_days = -1
    prev_count = -1
    for tier in raw_tiers:
        if not isinstance(tier, dict):
            raise DecayConfigError("each tier must be an object")
        marker = sanitize_marker(tier.get("marker"))
        max_days = _validate_threshold(
            tier.get("max_days"), "max_days", False, MAX_DECAY_DAYS
        )
        max_count = _validate_threshold(
            tier.get("max_count"), "max_count", True, MAX_DECAY_COUNT
        )
        # tiers must be strictly increasing on both axes (loud -> quiet ladder)
        if max_days <= prev_days or max_count < prev_count:
            raise DecayConfigError("tiers must increase by days and messages")
        prev_days, prev_count = max_days, max_count
        tiers.append({"marker": marker, "max_days": max_days, "max_count": max_count})

    floor_marker = sanitize_marker(data.get("floor_marker"))

    auto_trust = data.get("auto_trust")
    if auto_trust is not None:
        if not isinstance(auto_trust, dict):
            raise DecayConfigError("auto_trust must be null or an object")
        min_days = _validate_threshold(
            auto_trust.get("min_days"), "min_days", False, MAX_DECAY_DAYS
        )
        min_count = _validate_threshold(
            auto_trust.get("min_count"), "min_count", True, MAX_DECAY_COUNT
        )
        # auto-trust must sit at or below the last (longest) tier
        if min_days < prev_days or min_count < prev_count:
            raise DecayConfigError(
                "auto-trust thresholds must be at least the last decay tier"
            )
        auto_trust = {"min_days": min_days, "min_count": min_count}

    return {"tiers": tiers, "floor_marker": floor_marker, "auto_trust": auto_trust}


def get_decay_config(user) -> dict:
    """Return the user's decay config, or a copy of the defaults.

    Falls back to defaults if the stored config is missing or somehow invalid, so a
    bad row can never break mail delivery.
    """
    raw = getattr(user, "sender_warning_decay", None)
    if not raw:
        return copy.deepcopy(DEFAULT_DECAY)
    try:
        return validate_decay_config(raw)
    except DecayConfigError:
        LOG.w("Invalid sender_warning_decay for user %s, using defaults", user)
        return copy.deepcopy(DEFAULT_DECAY)


def decay_count_bound(user) -> int:
    """Smallest email count that saturates every decay decision for this user.

    The marker/auto-trust logic only compares the contact's message count against the
    tier `max_count` thresholds and the optional `auto_trust.min_count`; the exact total
    above the largest of those is never used. So counting past this bound is wasted work.
    Returns largest relevant threshold + 1 (so a value strictly greater than every
    threshold is still distinguishable from a value that merely equals one).
    """
    cfg = get_decay_config(user)
    thresholds = [t["max_count"] for t in cfg["tiers"]]
    auto_trust = cfg.get("auto_trust")
    if auto_trust:
        thresholds.append(auto_trust["min_count"])
    return max(thresholds) + 1


def bounded_contact_email_count(contact: Contact, user) -> int:
    """Contact's forwarded-message count, capped at decay_count_bound(user).

    Replaces an unbounded COUNT over the contact's email_log rows on the mail hot path
    (and the per-contact dashboard state): the decay ladder saturates at a handful of
    messages, so a bounded LIMIT scan is O(bound) instead of O(history) yet yields an
    identical marker. Capped values still exceed every threshold, so tier selection is
    unchanged.
    """
    bound = decay_count_bound(user)
    return EmailLog.filter_by(contact_id=contact.id).limit(bound).count()


def get_configured_markers(user) -> list:
    """All marker glyphs currently in use (tiers + floor), longest first.

    Longest-first so reply-phase stripping removes a two-glyph marker before a
    one-glyph substring of it.
    """
    cfg = get_decay_config(user)
    markers = [t["marker"] for t in cfg["tiers"]] + [cfg["floor_marker"]]
    # de-dup while keeping order, then sort longest-first
    seen = []
    for m in markers:
        if m not in seen:
            seen.append(m)
    return sorted(seen, key=len, reverse=True)


def _contact_age_days(contact: Contact) -> float:
    if not contact.created_at:
        return 0.0
    return (arrow.utcnow() - contact.created_at).total_seconds() / 86400


def warning_tier_index(contact: Contact, email_log_count: int, user=None) -> int:
    """Decay tier a non-trusted sender falls in: 0..len(tiers)-1 for the configured
    tiers, or len(tiers) for the floor.

    A tier holds while the contact is younger than its max_days OR has no more than
    max_count messages (conservative -- stays loud while recent or sparse). This is
    presentation-free; the glyph and the dashboard label are both derived from it.
    """
    cfg = get_decay_config(user)
    age_days = _contact_age_days(contact)
    for i, tier in enumerate(cfg["tiers"]):
        if age_days < tier["max_days"] or email_log_count <= tier["max_count"]:
            return i
    return len(cfg["tiers"])


def get_warning_marker(contact: Contact, email_log_count: int, user=None) -> str:
    """Marker glyph for a non-trusted sender, per the user's decay ladder (or defaults).

    Used by the email path to inject the actual glyph. With no user, the built-in
    default ladder is used.
    """
    cfg = get_decay_config(user)
    markers = [t["marker"] for t in cfg["tiers"]] + [cfg["floor_marker"]]
    return markers[warning_tier_index(contact, email_log_count, user)]


TRUSTED_STATE = -1


def warning_state_for_contact(contact: Contact, email_log_count: int, user=None):
    """Semantic dashboard state for a contact's marker (presentation-free).

    None when nothing should be shown; TRUSTED_STATE (-1) for a trusted sender; else
    the warning tier index (0..len(tiers)). The client maps this to the glyph and the
    label; the email path uses get_warning_marker for the actual glyph. Centralizes
    the visibility rules so the panel builder and the single-contact endpoint agree.
    """
    user = user or contact.alias.user
    if not user.sender_warnings_enabled:
        return None
    if contact.block_forward:
        return None
    if not contact.alias.sender_allow_list:
        return None
    if contact.domain_in_allow_list:
        return TRUSTED_STATE
    return warning_tier_index(contact, email_log_count, user)


def should_auto_trust(contact: Contact, email_log_count: int, user=None) -> bool:
    """Whether a long-known sender should be promoted to the trusted set.

    Auto-trust fires only when the contact is at least min_days old AND has at least
    min_count messages (reluctant -- earned on both axes). Off when not configured.
    """
    auto_trust = get_decay_config(user)["auto_trust"]
    if not auto_trust:
        return False
    return (
        _contact_age_days(contact) >= auto_trust["min_days"]
        and email_log_count >= auto_trust["min_count"]
    )


_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def is_valid_registered_domain(domain: str) -> bool:
    """Validate a registered domain before it is stored or returned.

    tldextract normalizes input but does not reject it, so this guards the API
    boundary against malformed or unsafe values (HTML metacharacters, whitespace,
    control characters, bad structure).

    Accepts any legal hostname, including internationalized (IDN) domains: the
    value is converted to its ASCII (punycode) form via IDNA, then checked against
    the letter-digit-hyphen rule (RFC 952 / RFC 1123) — dotted labels of 1-63
    chars, each starting and ending alphanumeric, total <= 253. IDNA conversion
    rejects illegal labels (bad characters, empty, over-length) on its own; the
    LDH check is the explicit final gate.
    """
    if not domain or "." not in domain:
        return False
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    if len(ascii_domain) > 253:
        return False
    return all(_DOMAIN_LABEL_RE.match(label) for label in ascii_domain.split("."))


def ui_tag_for_contact(contact: Contact):
    """Dashboard marker state for a single contact (see warning_state_for_contact).

    None unless the feature is on and the alias has an active allow-list. For rendering
    many contacts at once, prefer build_contact_markers, which computes all states from
    a single batched message count.
    """
    emails_count = bounded_contact_email_count(contact, contact.alias.user)
    return warning_state_for_contact(contact, emails_count, contact.alias.user)


def build_contact_markers(alias: Alias, contacts) -> dict:
    """Per-contact dashboard markers for a specific set of contacts.

    Used to render only the contacts on the current page, so a contact-page load
    never aggregates markers across every contact of the alias. Message counts are
    fetched in one grouped query bounded to the given contacts.
    """
    if not contacts:
        return {}
    ids = [c.id for c in contacts]
    counts = dict(
        Session.query(EmailLog.contact_id, func.count(EmailLog.id))
        .filter(EmailLog.contact_id.in_(ids))
        .group_by(EmailLog.contact_id)
        .all()
    )
    markers = {}
    for c in contacts:
        state = warning_state_for_contact(c, counts.get(c.id, 0), alias.user)
        if state is not None:
            markers[str(c.id)] = state
    return markers


# Above this many marked domains the panel stops listing them all and shows only the
# domains actionable from the current page (visible contacts + focus); the remainder is
# reported via marked_total / has_more. At or below it, every marked domain is listed.
MARKED_PANEL_LIMIT = 50


def build_allow_list_state(
    alias: Alias, focus_domain: str = None, visible_ids=None
) -> dict:
    """Trusted/marked domain groups for the dashboard panel.

    Aggregated across all of the alias's contacts (the domain groups need every
    contact). When there are at most MARKED_PANEL_LIMIT marked domains they are all
    listed; when there are more, only the domains actionable from the current page are
    listed — an explicit focus_domain plus the domains of the visible contacts
    (visible_ids), pinned first — and the remainder is reported via marked_total /
    has_more (+N more). So the panel is always bounded yet always able to act on what
    is on screen.

    Per-contact markers are NOT included here: the contact page paints markers for its
    visible contacts (build_contact_markers), so the panel never carries all-contact
    tags.
    """
    trusted_set = alias.get_sender_allow_domains()
    contacts = Contact.filter_by(alias_id=alias.id).all()

    domain_counts: Counter = Counter()
    for contact in contacts:
        domain = contact.registered_domain
        if domain:
            domain_counts[domain] += 1

    trusted = [
        {
            "domain": domain,
            "contacts": domain_counts.get(domain, 0),
            "orphan": domain_counts.get(domain, 0) == 0,
        }
        for domain in sorted(trusted_set)
    ]

    marked_total = sum(1 for d in domain_counts if d not in trusted_set)

    # domains guaranteed in the list: an explicit focus first, then the domains of the
    # contacts visible on the page. Kept only if marked (present, not trusted), deduped.
    visible_set = set(visible_ids or [])
    pin_order = [focus_domain] if focus_domain else []
    pin_order += [
        c.registered_domain
        for c in contacts
        if c.id in visible_set and c.registered_domain
    ]
    pinned = []
    for d in pin_order:
        if d in domain_counts and d not in trusted_set and d not in pinned:
            pinned.append(d)

    if marked_total <= MARKED_PANEL_LIMIT:
        # few enough to list every marked domain (pinned first, then the rest)
        rest = sorted(
            d for d in domain_counts if d not in trusted_set and d not in pinned
        )
        marked_domains = pinned + rest
    else:
        # too many: list only the domains actionable from the current page; the
        # remainder is reported via marked_total / has_more
        marked_domains = pinned
    marked = [{"domain": d, "contacts": domain_counts[d]} for d in marked_domains]

    return {
        "trusted": trusted,
        "marked": marked,
        "marked_total": marked_total,
        "has_more": marked_total > len(marked),
        "counts": {"trusted": len(trusted_set), "marked": marked_total},
    }


def insert_marker_in_subject(text: str, tag: str) -> str:
    text = text or ""
    if not text:
        return tag

    non_consec_space_indexes = []
    for i, char in enumerate(text):
        if char == " ":
            if i == 0 or text[i - 1] != " ":
                non_consec_space_indexes.append(i)

    if len(non_consec_space_indexes) >= 3:
        target_idx = non_consec_space_indexes[2]
    elif len(non_consec_space_indexes) >= 2:
        target_idx = non_consec_space_indexes[1]
    elif len(non_consec_space_indexes) >= 1:
        target_idx = non_consec_space_indexes[0]
    else:
        return text + tag

    return text[:target_idx] + f" {tag} " + text[target_idx + 1 :]


def insert_marker_in_from(text: str, tag: str) -> str:
    text = text or ""
    if not text:
        return tag

    non_consec_space_indexes = []
    for i, char in enumerate(text):
        if char == " ":
            if i == 0 or text[i - 1] != " ":
                non_consec_space_indexes.append(i)

    if non_consec_space_indexes:
        target_idx = non_consec_space_indexes[0]
        return text[:target_idx] + f" {tag} " + text[target_idx + 1 :]
    else:
        return text + tag


def apply_marker_to_subject(
    msg: Message, tag: str, contact: Contact, alias: Alias
) -> None:
    current_subject = msg["Subject"]
    current_subject = get_header_unicode(current_subject) or ""
    new_subject = insert_marker_in_subject(current_subject, tag)

    # encode using utf-8 pattern
    new_subject_encoded = Header(new_subject, "utf-8").encode()
    add_or_replace_header(msg, "Subject", new_subject_encoded)
    LOG.d(
        "Unexpected sender: inserted marker into subject for %s -> %s",
        contact.website_email,
        alias,
    )


def apply_marker_to_from(
    new_from_header: str, tag: str, contact: Contact, alias: Alias
) -> str:
    try:
        display_name, email_address = parse_full_address(new_from_header)
    except ValueError:
        display_name, email_address = "", new_from_header

    new_display_name = insert_marker_in_from(display_name, tag)
    encoded_name = Header(new_display_name, "utf-8").encode()
    final_from_header = formataddr((encoded_name, email_address))
    LOG.d(
        "Unexpected sender: inserted marker into From for %s -> %s",
        contact.website_email,
        alias,
    )
    return final_from_header


def strip_marker_from_subject(subject: str, user) -> str:
    """Remove a previously-inserted marker from a reply-phase subject.

    insert_marker_in_subject produces exactly two shapes: the marker wrapped in
    single spaces mid-subject (" <tag> ", where it replaced an existing space), or
    the marker appended with no separator when the subject had no usable space.
    Reverse those two shapes only, so the marker is removed regardless of subject
    length or any reply/forward prefix the client prepended, without touching
    unrelated content. Markers are matched against the user's current glyphs,
    longest first (so a two-glyph marker is tried before a one-glyph substring).
    """
    if not subject:
        return subject
    for tag in get_configured_markers(user):
        if not tag:
            continue
        spaced = f" {tag} "
        if spaced in subject:
            # mid-subject insertion: collapse " <tag> " back to the single space
            return subject.replace(spaced, " ", 1)
        if subject.endswith(tag):
            # appended (no-separator) insertion on a space-less subject
            return subject[: -len(tag)]
        if subject.startswith(f"{tag} "):
            # marker landed at the very start (subject began with a space)
            return subject[len(tag) + 1 :]
    return subject
