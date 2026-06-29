"""Fork-custom: soft allow-list ("whitelist") tag manipulation.

This module is NOT part of upstream simple-login/app. It holds the logic for
inserting whitelist-mismatch markers into forwarded emails' Subject / From
headers, extracted out of ``email_handler.py`` so the custom behaviour lives in
a single file and stays clear of upstream merge conflicts.

See PR: "Refactor whitelist tag manipulation to utility module".
"""

import arrow
from email.header import Header
from email.message import Message
from email.utils import formataddr

from app.log import LOG
from app.models import Contact, Alias
from app.email_utils import (
    add_or_replace_header,
    get_header_unicode,
    parse_full_address,
)


def get_whitelist_tag(contact: Contact, email_log_count: int) -> str:
    now = arrow.utcnow()
    diff = (now - contact.created_at).total_seconds() / 3600

    if diff < 24 or email_log_count <= 2:
        return "⚠️⚠️"
    elif diff < 192 or email_log_count <= 5:
        return "⚠️"
    else:
        return "〰️"


def insert_tag_subject(text: str, tag: str) -> str:
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


def insert_tag_from(text: str, tag: str) -> str:
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


def apply_whitelist_tag_to_subject(
    msg: Message, tag: str, contact: Contact, alias: Alias
) -> None:
    current_subject = msg["Subject"]
    current_subject = get_header_unicode(current_subject) or ""
    new_subject = insert_tag_subject(current_subject, tag)

    # encode using utf-8 pattern
    new_subject_encoded = Header(new_subject, "utf-8").encode()
    add_or_replace_header(msg, "Subject", new_subject_encoded)
    LOG.d(
        "Whitelist mismatch: inserted into subject for %s -> %s",
        contact.website_email,
        alias,
    )


def apply_whitelist_tag_to_from(
    new_from_header: str, tag: str, contact: Contact, alias: Alias
) -> str:
    try:
        display_name, email_address = parse_full_address(new_from_header)
    except ValueError:
        display_name, email_address = "", new_from_header

    new_display_name = insert_tag_from(display_name, tag)
    encoded_name = Header(new_display_name, "utf-8").encode()
    final_from_header = formataddr((encoded_name, email_address))
    LOG.d(
        "Whitelist mismatch: inserted into From for %s -> %s",
        contact.website_email,
        alias,
    )
    return final_from_header
