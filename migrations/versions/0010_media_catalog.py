"""Add the owner-scoped media resource catalog and deterministic execution plans."""

from __future__ import annotations

import json
import secrets
import time

from alembic import op
import sqlalchemy as sa


revision = "0010_media_catalog"
down_revision = "0009_task_models"
branch_labels = None
depends_on = None


def _json_list(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _legacy_resource(user_id: str, preferences: dict, stamp: int) -> list[dict]:
    resources = []
    image_provider = str(preferences.get("image_provider") or "disabled").lower()
    if image_provider != "disabled":
        if image_provider == "openai":
            provider_key = "openai-image"
            backend = "openai"
            external_id = "provider-default"
            name = "Imported OpenAI image default"
            settings = {
                "size": preferences.get("image_size") or "1024x1024",
                "quality": preferences.get("image_quality") or "auto",
            }
            content_tags = ["general"]
        else:
            provider_key = "local-image"
            backend = str(preferences.get("image_local_backend") or "automatic1111").lower()
            if backend not in {"automatic1111", "comfyui"}:
                backend = "automatic1111"
            external_id = str(preferences.get("image_local_model") or "provider-default").strip()
            name = f"Imported {backend} image model"
            settings = {
                "size": preferences.get("image_size") or "1024x1024",
                "quality": preferences.get("image_quality") or "auto",
                "steps": preferences.get("image_local_steps"),
                "cfg_scale": preferences.get("image_local_cfg_scale"),
                "sampler_name": preferences.get("image_local_sampler_name"),
                "scheduler": preferences.get("image_local_scheduler"),
                "allow_nsfw": bool(preferences.get("image_local_allow_nsfw", False)),
            }
            content_tags = ["general", "adult", "nudity", "explicit"] if settings["allow_nsfw"] else ["general"]
        resources.append(
            {
                "id": secrets.token_hex(12),
                "user_id": user_id,
                "resource_type": "model",
                "kind": "image",
                "name": name,
                "provider_key": provider_key,
                "backend": backend,
                "external_id": external_id,
                "enabled": 1,
                "priority": 50,
                "operations_json": '["generate"]',
                "domains_json": "[]",
                "content_tags_json": _json_list(content_tags),
                "features_json": '["text_to_image"]',
                "estimated_vram_mb": 0,
                "estimated_load_seconds": 0,
                "default_settings_json": json.dumps(settings, separators=(",", ":"), default=str),
                "notes": "Imported from the pre-catalog image settings.",
                "revision": 1,
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
    video_provider = str(preferences.get("video_provider") or "disabled").lower()
    if video_provider == "openai":
        resources.append(
            {
                "id": secrets.token_hex(12),
                "user_id": user_id,
                "resource_type": "model",
                "kind": "video",
                "name": "Imported OpenAI video model",
                "provider_key": "openai-video",
                "backend": "openai",
                "external_id": str(preferences.get("video_model") or "sora-2"),
                "enabled": 1,
                "priority": 50,
                "operations_json": '["generate"]',
                "domains_json": "[]",
                "content_tags_json": '["general"]',
                "features_json": '["text_to_video"]',
                "estimated_vram_mb": 0,
                "estimated_load_seconds": 0,
                "default_settings_json": json.dumps(
                    {
                        "size": preferences.get("video_size") or "720x1280",
                        "seconds": preferences.get("video_duration") or "4",
                    },
                    separators=(",", ":"),
                ),
                "notes": "Imported from the pre-catalog video settings.",
                "revision": 1,
                "created_at": stamp,
                "updated_at": stamp,
            }
        )
    return resources


def upgrade():
    op.create_table(
        "media_catalog_settings",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("vram_budget_mb", sa.Integer(), nullable=False, server_default="10240"),
        sa.Column("max_loras", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("legacy_imported", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("vram_budget_mb BETWEEN 0 AND 131072", name="ck_media_catalog_vram_budget"),
        sa.CheckConstraint("max_loras BETWEEN 0 AND 8", name="ck_media_catalog_max_loras"),
        sa.CheckConstraint("legacy_imported IN (0,1)", name="ck_media_catalog_legacy_imported"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "media_catalog_resources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider_key", sa.Text(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("operations_json", sa.Text(), nullable=False, server_default='["generate"]'),
        sa.Column("domains_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("features_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("estimated_vram_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_load_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("default_settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("resource_type IN ('model','lora','workflow')", name="ck_media_resource_type"),
        sa.CheckConstraint("kind IN ('image','video')", name="ck_media_resource_kind"),
        sa.CheckConstraint(
            "provider_key IN ('openai-image','local-image','openai-video')",
            name="ck_media_resource_provider",
        ),
        sa.CheckConstraint("backend IN ('openai','automatic1111','comfyui')", name="ck_media_resource_backend"),
        sa.CheckConstraint("enabled IN (0,1)", name="ck_media_resource_enabled"),
        sa.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_media_resource_priority"),
        sa.CheckConstraint("estimated_vram_mb BETWEEN 0 AND 131072", name="ck_media_resource_vram"),
        sa.CheckConstraint("estimated_load_seconds BETWEEN 0 AND 3600", name="ck_media_resource_load"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id",
            "resource_type",
            "provider_key",
            "backend",
            "external_id",
            name="uq_media_resource_external",
        ),
    )
    op.create_index("idx_media_resources_user_enabled", "media_catalog_resources", ["user_id", "enabled", "kind"])
    op.create_index("idx_media_resources_user_type", "media_catalog_resources", ["user_id", "resource_type", "kind"])
    op.create_table(
        "media_resource_compatibility",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("model_resource_id", sa.Text(), nullable=False),
        sa.CheckConstraint("resource_id <> model_resource_id", name="ck_media_resource_not_self_compatible"),
        sa.ForeignKeyConstraint(["resource_id"], ["media_catalog_resources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_resource_id"], ["media_catalog_resources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("resource_id", "model_resource_id", name="uq_media_resource_compatibility"),
    )
    op.create_index("idx_media_compatibility_model", "media_resource_compatibility", ["model_resource_id"])
    op.create_table(
        "media_execution_plans",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("capability_request_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("requirements_json", sa.Text(), nullable=False),
        sa.Column("selected_resources_json", sa.Text(), nullable=False),
        sa.Column("execution_options_json", sa.Text(), nullable=False),
        sa.Column("explanation_json", sa.Text(), nullable=False),
        sa.Column("estimated_vram_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_code", sa.Text(), nullable=True),
        sa.Column("block_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.CheckConstraint("source IN ('coordinator','manual')", name="ck_media_plan_source"),
        sa.CheckConstraint("status IN ('ready','blocked')", name="ck_media_plan_status"),
        sa.CheckConstraint("kind IN ('image','video')", name="ck_media_plan_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["capability_request_id"], ["capability_requests.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("capability_request_id", name="uq_media_plan_capability"),
    )
    op.create_index("idx_media_plans_user_created", "media_execution_plans", ["user_id", "created_at"])
    op.create_index("idx_media_plans_capability", "media_execution_plans", ["capability_request_id"])

    connection = op.get_bind()
    stamp = int(time.time())
    rows = connection.execute(
        sa.text(
            "SELECT users.id,app_settings.preferences_json FROM users LEFT JOIN app_settings ON app_settings.user_id=users.id"
        )
    ).fetchall()
    resource_table = sa.table(
        "media_catalog_resources",
        *[
            sa.column(name)
            for name in (
                "id",
                "user_id",
                "resource_type",
                "kind",
                "name",
                "provider_key",
                "backend",
                "external_id",
                "enabled",
                "priority",
                "operations_json",
                "domains_json",
                "content_tags_json",
                "features_json",
                "estimated_vram_mb",
                "estimated_load_seconds",
                "default_settings_json",
                "notes",
                "revision",
                "created_at",
                "updated_at",
            )
        ],
    )
    for user_id, raw_preferences in rows:
        try:
            preferences = json.loads(raw_preferences or "{}")
        except (TypeError, ValueError):
            preferences = {}
        if not isinstance(preferences, dict):
            preferences = {}
        connection.execute(
            sa.text(
                "INSERT INTO media_catalog_settings(user_id,vram_budget_mb,max_loras,legacy_imported,created_at,updated_at) "
                "VALUES(:user_id,10240,4,1,:stamp,:stamp)"
            ),
            {"user_id": user_id, "stamp": stamp},
        )
        resources = _legacy_resource(user_id, preferences, stamp)
        if resources:
            connection.execute(resource_table.insert(), resources)


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
