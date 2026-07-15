"""Restore catalog bootstrap for providers enabled after initial setup."""

from __future__ import annotations

import json
import secrets
import time

from alembic import op
import sqlalchemy as sa


revision = "0015_media_provider_bootstrap"
down_revision = "0014_media_correction_workflows"
branch_labels = None
depends_on = None


def _preferences(connection) -> dict[str, dict]:
    result = {}
    for user_id, raw in connection.execute(sa.text("SELECT user_id,preferences_json FROM app_settings")):
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            value = {}
        result[user_id] = value if isinstance(value, dict) else {}
    typed = {}
    for user_id, key, raw in connection.execute(
        sa.text("SELECT user_id,key,value_json FROM setting_values ORDER BY user_id,key")
    ):
        try:
            typed.setdefault(user_id, {})[key] = json.loads(raw)
        except (TypeError, ValueError):
            continue
    result.update(typed)
    return result


def _resource(user_id: str, kind: str, preferences: dict, stamp: int) -> dict | None:
    if kind == "image":
        provider = str(preferences.get("image_provider") or "disabled").strip().lower()
        if provider == "disabled":
            return None
        if provider == "openai":
            provider_key, backend, external_id = "openai-image", "openai", "provider-default"
            name = "Imported OpenAI image default"
            settings = {
                "size": preferences.get("image_size") or "1024x1024",
                "quality": preferences.get("image_quality") or "auto",
            }
            content_tags = ["general"]
        else:
            provider_key = "local-image"
            backend = str(preferences.get("image_local_backend") or "automatic1111").strip().lower()
            if provider == "local/comfyui":
                backend = "comfyui"
            elif provider == "local/automatic1111":
                backend = "automatic1111"
            if backend not in {"automatic1111", "comfyui"}:
                backend = "automatic1111"
            external_id = str(preferences.get("image_local_model") or "provider-default").strip()
            name = f"Imported {backend} image model"
            allow_nsfw = bool(preferences.get("image_local_allow_nsfw", False))
            settings = {
                "size": preferences.get("image_size") or "1024x1024",
                "quality": preferences.get("image_quality") or "auto",
                "steps": preferences.get("image_local_steps"),
                "cfg_scale": preferences.get("image_local_cfg_scale"),
                "sampler_name": preferences.get("image_local_sampler_name"),
                "scheduler": preferences.get("image_local_scheduler"),
                "allow_nsfw": allow_nsfw,
            }
            content_tags = ["general", "adult", "nudity", "explicit"] if allow_nsfw else ["general"]
        features = ["text_to_image"]
    else:
        if str(preferences.get("video_provider") or "disabled").strip().lower() != "openai":
            return None
        provider_key, backend = "openai-video", "openai"
        external_id = str(preferences.get("video_model") or "sora-2")
        name = "Imported OpenAI video model"
        settings = {
            "size": preferences.get("video_size") or "720x1280",
            "seconds": preferences.get("video_duration") or "4",
        }
        content_tags = ["general"]
        features = ["text_to_video"]
    return {
        "id": secrets.token_hex(12),
        "user_id": user_id,
        "resource_type": "model",
        "kind": kind,
        "name": name,
        "provider_key": provider_key,
        "backend": backend,
        "external_id": external_id,
        "enabled": 1,
        "priority": 50,
        "operations_json": '["generate"]',
        "domains_json": "[]",
        "content_tags_json": json.dumps(content_tags, separators=(",", ":")),
        "features_json": json.dumps(features, separators=(",", ":")),
        "estimated_vram_mb": 0,
        "estimated_load_seconds": 0,
        "default_settings_json": json.dumps(settings, separators=(",", ":"), default=str),
        "notes": f"Imported from {kind} settings after provider enablement.",
        "revision": 1,
        "created_at": stamp,
        "updated_at": stamp,
    }


def upgrade():
    connection = op.get_bind()
    stamp = int(time.time())
    table = sa.table(
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
    existing = {
        (user_id, kind)
        for user_id, kind in connection.execute(sa.text("SELECT DISTINCT user_id,kind FROM media_catalog_resources"))
    }
    resources = []
    for user_id, preferences in _preferences(connection).items():
        for kind in ("image", "video"):
            if (user_id, kind) in existing:
                continue
            value = _resource(user_id, kind, preferences, stamp)
            if value:
                resources.append(value)
    if resources:
        connection.execute(table.insert(), resources)


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
