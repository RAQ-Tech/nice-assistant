"""Rewrite stored artifact links for the canonical browser API."""

from __future__ import annotations

import urllib.parse

from alembic import op


revision = "0007_browser_v1_cutover"
down_revision = "0006_memory_v2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    media_rows = bind.exec_driver_sql(
        "SELECT id,user_id,chat_id,kind,filename FROM media_files ORDER BY created_at,id"
    ).fetchall()
    for media_id, user_id, chat_id, kind, filename in media_rows:
        if kind not in {"image", "video"}:
            continue
        collection = "images" if kind == "image" else "videos"
        old_url = f"/api/{collection}/{urllib.parse.quote(filename)}"
        new_url = f"/api/v1/media/{media_id}"
        if chat_id:
            bind.exec_driver_sql(
                "UPDATE messages SET text=replace(text,?,?) WHERE chat_id=? AND instr(text,?)>0",
                (old_url, new_url, chat_id, old_url),
            )
            bind.exec_driver_sql(
                "UPDATE conversation_summaries SET content=replace(content,?,?) WHERE chat_id=? AND instr(content,?)>0",
                (old_url, new_url, chat_id, old_url),
            )
        bind.exec_driver_sql(
            "UPDATE async_jobs SET result_json=replace(result_json,?,?) "
            "WHERE user_id=? AND result_json IS NOT NULL AND instr(result_json,?)>0",
            (old_url, new_url, user_id, old_url),
        )


def downgrade():
    # Production recovery is restore-based; migrations are intentionally forward-only.
    pass
