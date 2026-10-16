"""Remove the retired cloud-video provider without touching generated media.

Video has been local-only since the 2026-08-26 product decision. The former
cloud API stopped on 2026-09-24, so retaining its adapter and catalog rows now
creates settings that can never work.

The migration removes only provider configuration. Finished execution plans
and media are historical snapshots and remain intact. Presets that referenced
the retired resource are preserved but disabled so an operator can still see
what they configured without the planner attempting to use it.
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0041_remove_retired_cloud_video"
down_revision = "0040_local_video_provider"
branch_labels = None
depends_on = None

_OLD = "provider_key IN ('openai-image','local-image','openai-video','local-video')"
_NEW = "provider_key IN ('openai-image','local-image','local-video')"
_NOTE = "Retired cloud-video provider removed; this preset is preserved but disabled."


def _contains_resource(value, resource_ids: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_contains_resource(item, resource_ids) for item in value.values())
    if isinstance(value, list):
        return any(_contains_resource(item, resource_ids) for item in value)
    return isinstance(value, str) and value in resource_ids


def upgrade() -> None:
    bind = op.get_bind()
    resource_ids = {
        str(row[0])
        for row in bind.execute(sa.text("SELECT id FROM media_catalog_resources WHERE provider_key = 'openai-video'"))
    }

    if resource_ids:
        for row in bind.execute(sa.text("SELECT id, definition_json, notes FROM media_generation_presets")):
            try:
                definition = json.loads(row.definition_json or "{}")
            except (TypeError, ValueError):
                definition = {}
            if not _contains_resource(definition, resource_ids):
                continue
            notes = str(row.notes or "").strip()
            if _NOTE not in notes:
                notes = f"{notes}\n\n{_NOTE}".strip()
            bind.execute(
                sa.text("UPDATE media_generation_presets SET enabled = 0, notes = :notes WHERE id = :id"),
                {"id": row.id, "notes": notes},
            )

        bind.execute(
            sa.text(
                "DELETE FROM media_resource_compatibility "
                "WHERE resource_id IN (SELECT id FROM media_catalog_resources WHERE provider_key = 'openai-video') "
                "OR model_resource_id IN (SELECT id FROM media_catalog_resources WHERE provider_key = 'openai-video')"
            )
        )
        bind.execute(sa.text("DELETE FROM media_catalog_resources WHERE provider_key = 'openai-video'"))

    for row in bind.execute(sa.text("SELECT user_id, preferences_json FROM app_settings")):
        try:
            preferences = json.loads(row.preferences_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(preferences, dict):
            continue
        changed = False
        if (
            "video_provider" in preferences
            and str(preferences.get("video_provider") or "disabled").strip().lower() != "local"
        ):
            if preferences.get("video_provider") != "disabled":
                preferences["video_provider"] = "disabled"
                changed = True
        for key in ("video_model", "video_size", "video_duration"):
            if key in preferences:
                preferences.pop(key)
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE app_settings SET preferences_json = :preferences WHERE user_id = :user_id"),
                {"user_id": row.user_id, "preferences": json.dumps(preferences, separators=(",", ":"))},
            )

    with op.batch_alter_table("media_catalog_resources") as batch:
        batch.drop_constraint("ck_media_resource_provider", type_="check")
        batch.create_check_constraint("ck_media_resource_provider", _NEW)


def downgrade() -> None:
    # Provider configuration is not recreated: it could no longer execute.
    # Recovery of retired settings, if ever needed, comes from a pre-upgrade
    # backup. Widening the constraint is enough to make the schema reversible.
    with op.batch_alter_table("media_catalog_resources") as batch:
        batch.drop_constraint("ck_media_resource_provider", type_="check")
        batch.create_check_constraint("ck_media_resource_provider", _OLD)
