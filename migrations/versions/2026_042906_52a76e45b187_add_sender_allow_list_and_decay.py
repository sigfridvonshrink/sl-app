"""Add sender allow-list, warning-decay columns, and delivered-message index

Adds the two feature columns (alias.sender_allow_list, users.sender_warning_decay)
and the partial index that backs the bounded distinct-message count in
app/sender_warning_utils.bounded_contact_email_count: over delivered inbound forwards
only (is_reply = false, blocked = false, no refused email), on (contact_id,
message_id), so Postgres can stream distinct message_ids per contact and stop after a
few rows on the mail hot path.

Revision ID: 52a76e45b187
Revises: 4a9f8c2e1b3d
Create Date: 2026-04-29 06:16:36.286528

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "52a76e45b187"
down_revision = "4a9f8c2e1b3d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("alias", sa.Column("sender_allow_list", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("sender_warning_decay", sa.JSON(), nullable=True))
    op.create_index(
        "ix_email_log_contact_delivered_message",
        "email_log",
        ["contact_id", "message_id"],
        unique=False,
        postgresql_where=sa.text(
            "is_reply = false AND blocked = false AND refused_email_id IS NULL"
        ),
    )


def downgrade():
    op.drop_index(
        "ix_email_log_contact_delivered_message",
        table_name="email_log",
    )
    op.drop_column("users", "sender_warning_decay")
    op.drop_column("alias", "sender_allow_list")
