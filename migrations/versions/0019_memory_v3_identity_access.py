"""Add the Memory v3 identity, access, and validity persistence foundation."""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "0019_memory_v3_identity_access"
down_revision = "0018_human_image_delivery"
branch_labels = None
depends_on = None


def _human_id(user_id: str) -> str:
    digest = hashlib.sha256(f"nice-assistant-human-principal:{user_id}".encode()).hexdigest()
    return f"human-{digest[:24]}"


def _source_chat_id(bind, source_message_id, source_turn_id):
    message_chat_id = None
    turn_chat_id = None
    if source_message_id:
        row = bind.exec_driver_sql(
            "SELECT chat_id FROM messages WHERE id=?",
            (source_message_id,),
        ).fetchone()
        message_chat_id = row[0] if row else None
    if source_turn_id:
        row = bind.exec_driver_sql(
            "SELECT chat_id FROM conversation_turns WHERE id=?",
            (source_turn_id,),
        ).fetchone()
        turn_chat_id = row[0] if row else None
    if message_chat_id and turn_chat_id and message_chat_id != turn_chat_id:
        return None
    return message_chat_id or turn_chat_id


def upgrade():
    op.create_table(
        "human_principals",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "owner_profiles",
        sa.Column("human_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("name_pronunciation", sa.Text()),
        sa.Column("pronouns", sa.Text()),
        sa.Column("time_zone", sa.Text()),
        sa.Column("locale", sa.Text()),
        sa.Column("preferred_language", sa.Text()),
        sa.Column("measurement_units", sa.Text()),
        sa.Column("communication_needs", sa.Text()),
        sa.Column("accessibility_needs", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_owner_profiles_revision"),
        sa.ForeignKeyConstraint(["human_id"], ["human_principals.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "owner_profile_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('created','updated','cleared')",
            name="ck_owner_profile_events_action",
        ),
        sa.ForeignKeyConstraint(["human_id"], ["human_principals.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_owner_profile_events_human_created",
        "owner_profile_events",
        ["human_id", "created_at"],
    )

    op.create_table(
        "chat_bindings",
        sa.Column("chat_id", sa.Text(), primary_key=True),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text()),
        sa.Column("context_kind", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text()),
        sa.Column("binding_status", sa.Text(), nullable=False),
        sa.Column("persona_name_snapshot", sa.Text()),
        sa.Column("workspace_name_snapshot", sa.Text()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "context_kind IN ('personal','workspace','legacy_unresolved')",
            name="ck_chat_bindings_context_kind",
        ),
        sa.CheckConstraint(
            "binding_status IN ('active','legacy_unresolved')",
            name="ck_chat_bindings_status",
        ),
        sa.CheckConstraint(
            "(binding_status='active' AND persona_id IS NOT NULL AND "
            "((context_kind='personal' AND workspace_id IS NULL) OR "
            "(context_kind='workspace' AND workspace_id IS NOT NULL))) OR "
            "(binding_status='legacy_unresolved' AND context_kind='legacy_unresolved')",
            name="ck_chat_bindings_shape",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["human_id"], ["human_principals.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_chat_bindings_human_status",
        "chat_bindings",
        ["human_id", "binding_status"],
    )

    op.create_table(
        "memory_records",
        sa.Column("memory_id", sa.Text(), primary_key=True),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column(
            "lineage",
            sa.Text(),
            nullable=False,
            server_default="native_v3",
        ),
        sa.Column("access_state", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.Text(), nullable=False),
        sa.Column("validity_status", sa.Text(), nullable=False),
        sa.Column("valid_until", sa.Integer()),
        sa.Column("stateful_status", sa.Text()),
        sa.Column("last_confirmed_at", sa.Integer()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "lineage IN ('legacy_migrated','native_v3')",
            name="ck_memory_records_lineage",
        ),
        sa.CheckConstraint(
            "access_state IN ('grants','legacy_quarantined')",
            name="ck_memory_records_access_state",
        ),
        sa.CheckConstraint(
            "memory_type IN ('durable','temporal','stateful','legacy_unknown')",
            name="ck_memory_records_type",
        ),
        sa.CheckConstraint(
            "validity_status IN ('current','stale','expired','legacy_unknown')",
            name="ck_memory_records_validity",
        ),
        sa.CheckConstraint(
            "stateful_status IS NULL OR stateful_status IN ('active','completed','cancelled','superseded')",
            name="ck_memory_records_stateful_status",
        ),
        sa.CheckConstraint(
            "(lineage='legacy_migrated' AND access_state='legacy_quarantined' "
            "AND memory_type='legacy_unknown' "
            "AND validity_status='legacy_unknown' AND valid_until IS NULL "
            "AND stateful_status IS NULL AND last_confirmed_at IS NULL) OR "
            "(access_state='grants' AND memory_type IN ('durable','temporal','stateful') "
            "AND validity_status IN ('current','stale','expired') "
            "AND last_confirmed_at IS NOT NULL "
            "AND ((memory_type='temporal' AND valid_until IS NOT NULL) "
            "OR (memory_type!='temporal' AND valid_until IS NULL)) "
            "AND ((memory_type='stateful' AND stateful_status IS NOT NULL) "
            "OR (memory_type!='stateful' AND stateful_status IS NULL)))",
            name="ck_memory_records_shape",
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["human_id"], ["human_principals.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("memory_id", "human_id", name="uq_memory_records_memory_human"),
    )
    op.create_index(
        "idx_memory_records_human_current",
        "memory_records",
        ["human_id", "access_state", "validity_status", "memory_type"],
    )
    op.create_table(
        "memory_origins",
        sa.Column("memory_id", sa.Text(), primary_key=True),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_chat_id", sa.Text()),
        sa.Column("source_persona_id", sa.Text()),
        sa.Column("source_workspace_id", sa.Text()),
        sa.Column("source_message_id", sa.Text()),
        sa.Column("source_turn_id", sa.Text()),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provenance_status", sa.Text(), nullable=False),
        sa.Column("revision_of_memory_id", sa.Text()),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('legacy','manual','conversation','edit','owner_explicit')",
            name="ck_memory_origins_source_kind",
        ),
        sa.CheckConstraint(
            "provenance_status IN ('resolved','legacy_unresolved')",
            name="ck_memory_origins_provenance_status",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "human_id"],
            ["memory_records.memory_id", "memory_records.human_id"],
            ondelete="CASCADE",
            name="fk_memory_origins_record_owner",
        ),
    )
    op.create_index(
        "idx_memory_origins_human_chat",
        "memory_origins",
        ["human_id", "source_chat_id"],
    )
    op.create_table(
        "memory_grants",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column("grant_type", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text()),
        sa.Column("workspace_id", sa.Text()),
        sa.Column("grant_source", sa.Text(), nullable=False),
        sa.Column("granted_by_human_id", sa.Text(), nullable=False),
        sa.Column("granted_at", sa.Integer(), nullable=False),
        sa.Column("revoked_by_human_id", sa.Text()),
        sa.Column("revoked_at", sa.Integer()),
        sa.CheckConstraint(
            "grant_type IN ('persona','workspace')",
            name="ck_memory_grants_type",
        ),
        sa.CheckConstraint(
            "(grant_type='persona' AND persona_id IS NOT NULL AND workspace_id IS NULL) OR "
            "(grant_type='workspace' AND workspace_id IS NOT NULL AND persona_id IS NULL)",
            name="ck_memory_grants_target",
        ),
        sa.CheckConstraint(
            "grant_source IN ('owner','automatic_source_persona')",
            name="ck_memory_grants_source",
        ),
        sa.CheckConstraint(
            "grant_source!='automatic_source_persona' OR grant_type='persona'",
            name="ck_memory_grants_automatic_target",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_human_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_human_id IS NOT NULL AND revoked_at>=granted_at)",
            name="ck_memory_grants_revocation",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "human_id"],
            ["memory_records.memory_id", "memory_records.human_id"],
            ondelete="CASCADE",
            name="fk_memory_grants_record_owner",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_human_id"],
            ["human_principals.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_human_id"],
            ["human_principals.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "id",
            "memory_id",
            "human_id",
            name="uq_memory_grants_id_memory_human",
        ),
    )
    op.create_index(
        "idx_memory_grants_human_memory",
        "memory_grants",
        ["human_id", "memory_id"],
    )
    op.create_index(
        "idx_memory_grants_active_persona",
        "memory_grants",
        ["human_id", "persona_id", "memory_id"],
        sqlite_where=sa.text("revoked_at IS NULL AND grant_type='persona'"),
    )
    op.create_index(
        "idx_memory_grants_active_workspace",
        "memory_grants",
        ["human_id", "workspace_id", "memory_id"],
        sqlite_where=sa.text("revoked_at IS NULL AND grant_type='workspace'"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_grants_active_persona "
        "ON memory_grants(memory_id,persona_id) "
        "WHERE revoked_at IS NULL AND grant_type='persona'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_grants_active_workspace "
        "ON memory_grants(memory_id,workspace_id) "
        "WHERE revoked_at IS NULL AND grant_type='workspace'"
    )
    op.create_table(
        "memory_grant_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("grant_id", sa.Text(), nullable=False),
        sa.Column("human_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("grant_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "action IN ('granted','revoked')",
            name="ck_memory_grant_events_action",
        ),
        sa.CheckConstraint(
            "grant_type IN ('persona','workspace')",
            name="ck_memory_grant_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["memory_id", "human_id"],
            ["memory_records.memory_id", "memory_records.human_id"],
            ondelete="CASCADE",
            name="fk_memory_grant_events_record_owner",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "memory_id", "human_id"],
            ["memory_grants.id", "memory_grants.memory_id", "memory_grants.human_id"],
            ondelete="CASCADE",
            name="fk_memory_grant_events_grant_owner",
        ),
    )
    op.create_index(
        "idx_memory_grant_events_memory_created",
        "memory_grant_events",
        ["memory_id", "created_at"],
    )

    bind = op.get_bind()
    humans_by_user = {}
    for user_id, created_at in bind.exec_driver_sql("SELECT id,created_at FROM users ORDER BY id").fetchall():
        human_id = _human_id(str(user_id))
        stamp = int(created_at)
        humans_by_user[str(user_id)] = human_id
        bind.exec_driver_sql(
            "INSERT INTO human_principals(id,user_id,created_at,updated_at) VALUES(?,?,?,?)",
            (human_id, user_id, stamp, stamp),
        )
        bind.exec_driver_sql(
            "INSERT INTO owner_profiles("
            "human_id,name,name_pronunciation,pronouns,time_zone,locale,preferred_language,"
            "measurement_units,communication_needs,accessibility_needs,revision,created_at,updated_at"
            ") VALUES(?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,?,?)",
            (human_id, stamp, stamp),
        )

    for (
        chat_id,
        user_id,
        persona_id,
        workspace_id,
        created_at,
    ) in bind.exec_driver_sql("SELECT id,user_id,persona_id,workspace_id,created_at FROM chats ORDER BY id").fetchall():
        persona_name = None
        workspace_name = None
        if persona_id:
            row = bind.exec_driver_sql("SELECT name FROM personas WHERE id=?", (persona_id,)).fetchone()
            persona_name = row[0] if row else None
        if workspace_id:
            row = bind.exec_driver_sql("SELECT name FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            workspace_name = row[0] if row else None
        bind.exec_driver_sql(
            "INSERT INTO chat_bindings("
            "chat_id,human_id,persona_id,context_kind,workspace_id,binding_status,"
            "persona_name_snapshot,workspace_name_snapshot,created_at"
            ") VALUES(?,?,?,'legacy_unresolved',?,'legacy_unresolved',?,?,?)",
            (
                chat_id,
                humans_by_user[str(user_id)],
                persona_id,
                workspace_id,
                persona_name,
                workspace_name,
                created_at,
            ),
        )

    memory_rows = bind.exec_driver_sql(
        "SELECT id,user_id,source_type,source_message_id,source_turn_id,supersedes_id,"
        "created_at,updated_at FROM memories ORDER BY id"
    ).fetchall()
    for (
        memory_id,
        user_id,
        source_type,
        source_message_id,
        source_turn_id,
        supersedes_id,
        created_at,
        updated_at,
    ) in memory_rows:
        human_id = humans_by_user[str(user_id)]
        bind.exec_driver_sql(
            "INSERT INTO memory_records("
            "memory_id,human_id,lineage,access_state,memory_type,validity_status,valid_until,"
            "stateful_status,last_confirmed_at,created_at,updated_at"
            ") VALUES(?,?,'legacy_migrated','legacy_quarantined','legacy_unknown',"
            "'legacy_unknown',NULL,NULL,NULL,?,?)",
            (memory_id, human_id, created_at, updated_at),
        )
        source_chat_id = _source_chat_id(bind, source_message_id, source_turn_id)
        source_persona_id = None
        source_workspace_id = None
        if source_chat_id:
            source_chat = bind.exec_driver_sql(
                "SELECT persona_id,workspace_id FROM chats WHERE id=? AND user_id=?",
                (source_chat_id, user_id),
            ).fetchone()
            if source_chat:
                source_persona_id, source_workspace_id = source_chat
            else:
                source_chat_id = None
        preserved_kind = str(source_type or "legacy")
        if preserved_kind not in {"legacy", "manual", "conversation", "edit"}:
            preserved_kind = "legacy"
        bind.exec_driver_sql(
            "INSERT INTO memory_origins("
            "memory_id,human_id,source_kind,source_chat_id,source_persona_id,"
            "source_workspace_id,source_message_id,source_turn_id,evidence_json,"
            "provenance_status,revision_of_memory_id,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,'legacy_unresolved',?,?)",
            (
                memory_id,
                human_id,
                preserved_kind,
                source_chat_id,
                source_persona_id,
                source_workspace_id,
                source_message_id,
                source_turn_id,
                json.dumps([], separators=(",", ":")),
                supersedes_id,
                created_at,
            ),
        )

    op.execute(
        "CREATE TRIGGER memory_grants_access_state_guard "
        "BEFORE INSERT ON memory_grants FOR EACH ROW "
        "WHEN NOT EXISTS("
        "SELECT 1 FROM memory_records "
        "WHERE memory_id=NEW.memory_id AND human_id=NEW.human_id "
        "AND access_state='grants'"
        ") "
        "BEGIN SELECT RAISE(ABORT,'memory grants require a grants-managed record'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_grants_one_way_revocation "
        "BEFORE UPDATE ON memory_grants FOR EACH ROW "
        "WHEN OLD.id IS NOT NEW.id "
        "OR OLD.memory_id IS NOT NEW.memory_id "
        "OR OLD.human_id IS NOT NEW.human_id "
        "OR OLD.grant_type IS NOT NEW.grant_type "
        "OR OLD.persona_id IS NOT NEW.persona_id "
        "OR OLD.workspace_id IS NOT NEW.workspace_id "
        "OR OLD.grant_source IS NOT NEW.grant_source "
        "OR OLD.granted_by_human_id IS NOT NEW.granted_by_human_id "
        "OR OLD.granted_at IS NOT NEW.granted_at "
        "OR OLD.revoked_by_human_id IS NOT NULL "
        "OR OLD.revoked_at IS NOT NULL "
        "OR NEW.revoked_by_human_id IS NULL "
        "OR NEW.revoked_at IS NULL "
        "BEGIN SELECT RAISE(ABORT,'memory grants are append-only except for one-way revocation'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_grants_delete_guard "
        "BEFORE DELETE ON memory_grants FOR EACH ROW "
        "WHEN EXISTS("
        "SELECT 1 FROM memory_records "
        "WHERE memory_id=OLD.memory_id AND human_id=OLD.human_id"
        ") "
        "BEGIN SELECT RAISE(ABORT,'memory grant cannot be removed while its memory exists'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_grant_events_immutable "
        "BEFORE UPDATE ON memory_grant_events FOR EACH ROW "
        "BEGIN SELECT RAISE(ABORT,'memory grant events are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_grant_events_delete_guard "
        "BEFORE DELETE ON memory_grant_events FOR EACH ROW "
        "WHEN EXISTS("
        "SELECT 1 FROM memory_records "
        "WHERE memory_id=OLD.memory_id AND human_id=OLD.human_id"
        ") "
        "AND EXISTS("
        "SELECT 1 FROM memory_grants "
        "WHERE id=OLD.grant_id AND memory_id=OLD.memory_id AND human_id=OLD.human_id"
        ") "
        "BEGIN SELECT RAISE(ABORT,'memory grant event cannot be removed while its grant exists'); END"
    )
    op.execute(
        "CREATE TRIGGER chat_bindings_owner_guard "
        "BEFORE INSERT ON chat_bindings FOR EACH ROW "
        "WHEN NOT EXISTS("
        "SELECT 1 FROM chats c "
        "JOIN human_principals h ON h.user_id=c.user_id "
        "WHERE c.id=NEW.chat_id AND h.id=NEW.human_id"
        ") "
        "BEGIN SELECT RAISE(ABORT,'chat binding human must own the chat'); END"
    )
    op.execute(
        "CREATE TRIGGER chat_bindings_identity_immutable "
        "BEFORE UPDATE OF human_id,persona_id,context_kind,workspace_id,binding_status "
        "ON chat_bindings FOR EACH ROW "
        "WHEN OLD.human_id IS NOT NEW.human_id "
        "OR OLD.persona_id IS NOT NEW.persona_id "
        "OR OLD.context_kind IS NOT NEW.context_kind "
        "OR OLD.workspace_id IS NOT NEW.workspace_id "
        "OR OLD.binding_status IS NOT NEW.binding_status "
        "BEGIN SELECT RAISE(ABORT,'chat binding identity and context are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER chat_bindings_delete_guard "
        "BEFORE DELETE ON chat_bindings FOR EACH ROW "
        "WHEN EXISTS(SELECT 1 FROM chats WHERE id=OLD.chat_id) "
        "BEGIN SELECT RAISE(ABORT,'chat binding cannot be removed while its chat exists'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_records_owner_guard "
        "BEFORE INSERT ON memory_records FOR EACH ROW "
        "WHEN NOT EXISTS("
        "SELECT 1 FROM memories m "
        "JOIN human_principals h ON h.user_id=m.user_id "
        "WHERE m.id=NEW.memory_id AND h.id=NEW.human_id"
        ") "
        "BEGIN SELECT RAISE(ABORT,'memory record human must own the memory'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_records_identity_immutable "
        "BEFORE UPDATE OF memory_id,human_id,lineage ON memory_records FOR EACH ROW "
        "WHEN OLD.memory_id IS NOT NEW.memory_id OR OLD.human_id IS NOT NEW.human_id "
        "OR OLD.lineage IS NOT NEW.lineage "
        "BEGIN SELECT RAISE(ABORT,'memory record ownership and lineage are immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_origins_immutable "
        "BEFORE UPDATE ON memory_origins FOR EACH ROW "
        "BEGIN SELECT RAISE(ABORT,'memory origin is immutable'); END"
    )
    op.execute(
        "CREATE TRIGGER memory_origins_delete_guard "
        "BEFORE DELETE ON memory_origins FOR EACH ROW "
        "WHEN EXISTS(SELECT 1 FROM memories WHERE id=OLD.memory_id) "
        "BEGIN SELECT RAISE(ABORT,'memory origin cannot be removed while its memory exists'); END"
    )


def downgrade():
    raise RuntimeError(
        "Memory v3 in-place downgrade is not supported because removing immutable "
        "bindings, provenance, and grants could broaden access. Restore the verified "
        "pre-migration database backup with the previous application image instead."
    )
