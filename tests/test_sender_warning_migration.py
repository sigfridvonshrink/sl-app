"""Upgrade/downgrade coverage for the sender allow-list + decay + index migration.

The project builds local/test DBs with db.create_all() rather than by running
migrations, so this drives the migration module directly against a throwaway
SQLite database and checks that upgrade adds the columns and the delivered-message
index and downgrade removes them (the two are exact inverses).
"""

import importlib.util

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    "migrations/versions/" "2026_042906_52a76e45b187_add_sender_allow_list_and_decay.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("sw_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(conn, table):
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _indexes(conn, table):
    return {i["name"] for i in sa.inspect(conn).get_indexes(table)}


def test_sender_warning_migration_upgrade_downgrade():
    mig = _load_migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE alias (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(
            sa.text(
                "CREATE TABLE email_log ("
                "id INTEGER PRIMARY KEY, contact_id INTEGER, message_id TEXT, "
                "is_reply BOOLEAN, blocked BOOLEAN, refused_email_id INTEGER)"
            )
        )
        mig.op = Operations(MigrationContext.configure(conn))

        mig.upgrade()
        assert "sender_allow_list" in _columns(conn, "alias")
        assert "sender_warning_decay" in _columns(conn, "users")
        assert "ix_email_log_contact_delivered_message" in _indexes(conn, "email_log")

        mig.downgrade()
        assert "sender_allow_list" not in _columns(conn, "alias")
        assert "sender_warning_decay" not in _columns(conn, "users")
        assert (
            "ix_email_log_contact_delivered_message"
            not in _indexes(conn, "email_log")
        )
