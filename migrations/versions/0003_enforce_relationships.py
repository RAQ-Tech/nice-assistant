"""Enforce relational ownership on adopted databases."""

from alembic import op
import sqlalchemy as sa


revision = "0003_enforce_relationships"
down_revision = "0002_adopt_existing_schema"
branch_labels = None
depends_on = None


RELATIONSHIPS = {
    "sessions": [("fk_sessions_user", "users", ["user_id"], ["id"], "CASCADE")],
    "workspaces": [("fk_workspaces_user", "users", ["user_id"], ["id"], "CASCADE")],
    "personas": [("fk_personas_workspace", "workspaces", ["workspace_id"], ["id"], "CASCADE")],
    "persona_workspace_links": [
        ("fk_persona_links_persona", "personas", ["persona_id"], ["id"], "CASCADE"),
        ("fk_persona_links_workspace", "workspaces", ["workspace_id"], ["id"], "CASCADE"),
    ],
    "chats": [
        ("fk_chats_user", "users", ["user_id"], ["id"], "CASCADE"),
        ("fk_chats_workspace", "workspaces", ["workspace_id"], ["id"], "SET NULL"),
        ("fk_chats_persona", "personas", ["persona_id"], ["id"], "SET NULL"),
    ],
    "messages": [("fk_messages_chat", "chats", ["chat_id"], ["id"], "CASCADE")],
    "memories": [("fk_memories_user", "users", ["user_id"], ["id"], "CASCADE")],
    "app_settings": [("fk_app_settings_user", "users", ["user_id"], ["id"], "CASCADE")],
    "setting_values": [("fk_setting_values_user", "users", ["user_id"], ["id"], "CASCADE")],
    "audio_files": [
        ("fk_audio_user", "users", ["user_id"], ["id"], "CASCADE"),
        ("fk_audio_persona", "personas", ["persona_id"], ["id"], "SET NULL"),
        ("fk_audio_chat", "chats", ["chat_id"], ["id"], "SET NULL"),
    ],
    "media_files": [
        ("fk_media_user", "users", ["user_id"], ["id"], "CASCADE"),
        ("fk_media_chat", "chats", ["chat_id"], ["id"], "SET NULL"),
    ],
    "async_jobs": [
        ("fk_jobs_user", "users", ["user_id"], ["id"], "CASCADE"),
        ("fk_jobs_chat", "chats", ["chat_id"], ["id"], "SET NULL"),
    ],
}


def _key(foreign_key):
    return (
        foreign_key.get("referred_table"),
        tuple(foreign_key.get("constrained_columns") or []),
        tuple(foreign_key.get("referred_columns") or []),
    )


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    missing_by_table = {}
    for table, relationships in RELATIONSHIPS.items():
        if table not in tables:
            continue
        existing = {_key(item) for item in inspector.get_foreign_keys(table)}
        missing = [item for item in relationships if (item[1], tuple(item[2]), tuple(item[3])) not in existing]
        if missing:
            missing_by_table[table] = missing

    # Refuse to hide corrupt ownership by deleting or reassigning rows.
    for table, relationships in missing_by_table.items():
        for _name, parent, local_columns, remote_columns, _ondelete in relationships:
            local = local_columns[0]
            remote = remote_columns[0]
            count = bind.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table} child LEFT JOIN {parent} parent ON child.{local}=parent.{remote} WHERE child.{local} IS NOT NULL AND parent.{remote} IS NULL"
            ).scalar_one()
            if count:
                raise RuntimeError(
                    f"Cannot add relationship {table}.{local} -> {parent}.{remote}: {count} orphan rows require review"
                )

    for table, relationships in missing_by_table.items():
        with op.batch_alter_table(table, recreate="always") as batch:
            for name, parent, local_columns, remote_columns, ondelete in relationships:
                batch.create_foreign_key(name, parent, local_columns, remote_columns, ondelete=ondelete)


def downgrade():
    pass
