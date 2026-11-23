"""Move the unexpected-sender user flags from bits 4-6 to bits 48-50

Fork-only data migration (not upstream). The fork first stored its three
User.flags bits at 4, 5 and 6; upstream then took bit 4 for
FLAG_REFERRAL_PROGRAM_PARTICIPANT. The fork's flags now live at 48, 49 and 50
(app/models.py), and this moves what existing users have set:
bit 4 -> 48 (FLAG_AUTO_TRUST_FIRST_CONTACT), 5 -> 49 (FLAG_MARKER_IN_SUBJECT),
6 -> 50 (FLAG_SENDER_WARNINGS), clearing 4-6.

Run once on a database whose bits 4-6 were written by the fork. It must not run on
a database where upstream already set bit 4 for referral participants.

Revision ID: de38b8a87afa
Revises: 52a76e45b187
Create Date: 2026-11-23 18:00:00

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "de38b8a87afa"
down_revision = "52a76e45b187"
branch_labels = None
depends_on = None

LOW = 0x70  # bits 4, 5, 6
HIGH = LOW << 44  # bits 48, 49, 50


def upgrade():
    op.execute(
        f"UPDATE users SET flags = (flags & ~{LOW}::bigint) | ((flags & {LOW}::bigint) << 44) "
        f"WHERE flags & {LOW}::bigint <> 0"
    )


def downgrade():
    op.execute(
        f"UPDATE users SET flags = (flags & ~{HIGH}::bigint) | ((flags & {HIGH}::bigint) >> 44) "
        f"WHERE flags & {HIGH}::bigint <> 0"
    )
