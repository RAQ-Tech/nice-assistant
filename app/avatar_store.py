"""A copy of every persona avatar, owned by this product.

An avatar was a URL pasted into a field, and the image lived wherever the URL
pointed - a website, a data blob, or another service's scratch folder. One of
them pointed into ComfyUI's output directory, and when ComfyUI reorganised, the
persona's face silently vanished. Nothing was deleted; the link rotted, and the
product had no copy.

So the product takes one. A pasted URL is fetched once and stored in a
directory this product owns, the persona is repointed at the stored copy, and
the original can move, die, or be cleaned without anything here noticing.
Personas that predate this are converted by a background pass, which also moves
the multi-megabyte data-URL avatars out of the database - nine personas were
adding around twelve megabytes to every persona listing.
"""

from __future__ import annotations

import base64
from hashlib import sha256
from pathlib import Path
import urllib.parse
import urllib.request

# Ample for a portrait, and a refusal past it: an avatar is looked at inside a
# circle a few rem wide, and a file this large is a mistake rather than detail.
MAX_AVATAR_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 20.0

_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


class AvatarUnavailable(Exception):
    """The image could not be fetched or was not an image."""


def avatar_file(avatar_dir: Path, persona_id: str) -> Path | None:
    """The stored copy for this persona, whichever format it arrived in."""

    for extension in _EXTENSIONS.values():
        candidate = Path(avatar_dir) / f"{persona_id}{extension}"
        if candidate.is_file():
            return candidate
    return None


def served_url(persona_id: str, digest: str) -> str:
    """Where the browser finds the copy.

    The digest rides along as a version: the URL changes when the picture does,
    which is what lets the endpoint serve with a long-lived cache header.
    """

    return f"/api/v1/personas/{persona_id}/avatar?v={digest[:8]}"


def is_served(url: str | None) -> bool:
    text = str(url or "")
    return text.startswith("/api/v1/personas/") and "/avatar" in text


def store_bytes(avatar_dir: Path, persona_id: str, content: bytes, content_type: str) -> str:
    """Write the copy and answer with its served URL.

    The file is read back and compared before anything points at it, because a
    torn write here would replace a broken avatar with a different broken
    avatar and call it healed.
    """

    extension = _EXTENSIONS.get(str(content_type or "").split(";")[0].strip().lower())
    if not extension:
        raise AvatarUnavailable(f"'{content_type}' is not an image type this store keeps.")
    if not content:
        raise AvatarUnavailable("The image was empty.")
    if len(content) > MAX_AVATAR_BYTES:
        raise AvatarUnavailable("The image is larger than an avatar can be.")
    directory = Path(avatar_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{persona_id}{extension}"
    target.write_bytes(content)
    if target.read_bytes() != content:
        raise AvatarUnavailable("The stored copy did not read back intact.")
    # One face per persona: a replacement in a new format retires the old file.
    for extension_other in _EXTENSIONS.values():
        stale = directory / f"{persona_id}{extension_other}"
        if stale != target and stale.exists():
            stale.unlink()
    return served_url(persona_id, sha256(content).hexdigest())


def refresh_from_file(avatar_dir: Path, persona_id: str) -> str | None:
    """A served URL for a copy that is already on disk.

    This is what lets an operator place a file in the store by hand and have
    the next background pass adopt it - the restore path when the original
    source is gone for good.
    """

    existing = avatar_file(avatar_dir, persona_id)
    if not existing:
        return None
    return served_url(persona_id, sha256(existing.read_bytes()).hexdigest())


def _decode_data_url(url: str) -> tuple[bytes, str]:
    header, _, payload = url.partition(",")
    if not payload:
        raise AvatarUnavailable("The data URL carries no image.")
    content_type = header[5:].split(";")[0].strip() or "image/png"
    if ";base64" in header:
        try:
            return base64.b64decode(payload, validate=False), content_type
        except Exception as exc:
            raise AvatarUnavailable("The data URL could not be decoded.") from exc
    return urllib.parse.unquote_to_bytes(payload), content_type


def _fetch(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "nice-assistant-avatar/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            content = response.read(MAX_AVATAR_BYTES + 1)
    except Exception as exc:
        raise AvatarUnavailable(f"The image could not be fetched ({exc.__class__.__name__}).") from exc
    if not content_type.startswith("image/"):
        raise AvatarUnavailable(f"The URL answered with '{content_type or 'no content type'}', not an image.")
    return content, content_type


def snapshot(avatar_dir: Path, persona_id: str, url: str, fetch=_fetch) -> str:
    """Take the product's own copy of whatever the URL shows right now."""

    text = str(url or "").strip()
    if text.startswith("data:"):
        content, content_type = _decode_data_url(text)
    elif text.startswith(("http://", "https://")):
        content, content_type = fetch(text)
    else:
        raise AvatarUnavailable("Only http(s) and data URLs can be copied.")
    return store_bytes(avatar_dir, persona_id, content, content_type)
