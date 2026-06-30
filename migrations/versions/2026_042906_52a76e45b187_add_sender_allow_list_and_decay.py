"""Add sender allow-list and warning-decay columns

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


def downgrade():
    op.drop_column("users", "sender_warning_decay")
    op.drop_column("alias", "sender_allow_list")
