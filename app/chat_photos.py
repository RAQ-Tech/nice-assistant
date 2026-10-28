"""A photo somebody sends into a conversation, made safe and small before it is kept.

The browser shrinks a photo before sending it, and that is a courtesy, not a
guarantee: here it is decoded, turned upright, stripped of everything but its
pixels - a phone photo carries where and when it was taken - and re-encoded at
a size a model that can see actually uses. What is stored is only that.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from app.service_errors import RequestError


# Larger than anything the browser sends; a ceiling on what is decoded at all.
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 40_000_000
# Vision models look at far less than a phone camera takes; past this, the
# extra pixels cost memory and prompt time and show the model nothing more.
LONGEST_SIDE = 1568
PHOTO_MIME_TYPE = "image/jpeg"


@dataclass(frozen=True)
class NormalizedPhoto:
    content: bytes
    width: int
    height: int


def photo_from_data_url(value: str) -> bytes:
    """The bytes of a `data:image/...;base64,` URL, or a refusal saying why not."""

    header, separator, payload = str(value or "").partition(",")
    if not separator or not header.startswith("data:image/") or not header.endswith(";base64"):
        raise RequestError("A photo must be sent as an image.", 400)
    if len(payload) > MAX_PHOTO_BYTES * 4 // 3 + 4:
        raise RequestError("That photo is too large to send.", 413)
    try:
        return base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RequestError("That photo could not be read.", 400) from exc


def normalize_chat_photo(content: bytes) -> NormalizedPhoto:
    if not content:
        raise RequestError("A photo with something in it is required.", 400)
    if len(content) > MAX_PHOTO_BYTES:
        raise RequestError("That photo is too large to send.", 413)
    try:
        with Image.open(BytesIO(content)) as source:
            source.verify()
        with Image.open(BytesIO(content)) as source:
            if source.width * source.height > MAX_PHOTO_PIXELS:
                raise RequestError("That photo is too large to send.", 413)
            # Upright first: the orientation lives in the metadata about to be dropped.
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((LONGEST_SIDE, LONGEST_SIDE), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
    except RequestError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RequestError("That is not a photo this can read.", 400) from exc
    return NormalizedPhoto(output.getvalue(), image.width, image.height)
