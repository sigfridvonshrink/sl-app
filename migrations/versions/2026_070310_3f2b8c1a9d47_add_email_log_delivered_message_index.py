"""Add partial index on email_log for the bounded delivered-message count

Supports app/sender_warning_utils.bounded_contact_email_count: a partial index
over delivered inbound forwards (is_reply = false, blocked = false, no refused
email) on (contact_id, message_id), so Postgres can stream distinct message_ids
per contact and stop after a few rows on the mail hot path.

Revision ID: 3f2b8c1a9d47
Revises: 52a76e45b187
Create Date: 2026-07-03 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3f2b8c1a9d47"
down_revision = "52a76e45b187"
branch_labels = None
depends_on = None


def upgrade():
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
