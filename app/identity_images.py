from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.service_errors import RequestError


MAX_REFERENCE_BYTES = 5 * 1024 * 1024
MAX_CANDIDATE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 36_000_000


@dataclass(frozen=True)
class NormalizedIdentityImage:
    content: bytes
    width: int
    height: int
    digest: str


def read_identity_image_file(path: Path, *, max_bytes: int) -> bytes:
    try:
        if path.stat().st_size > max_bytes:
            raise RequestError("The identity image exceeds the safe file-size limit.", 413)
        with path.open("rb") as source:
            content = source.read(max_bytes + 1)
    except RequestError:
        raise
    except OSError as exc:
        raise RequestError("The identity image file is unavailable.", 400) from exc
    if len(content) > max_bytes:
        raise RequestError("The identity image exceeds the safe file-size limit.", 413)
    return content


def normalize_identity_image(content: bytes, *, enforce_upload_limit: bool) -> NormalizedIdentityImage:
    if not content:
        raise RequestError("A non-empty image is required.", 400)
    if enforce_upload_limit and len(content) > MAX_REFERENCE_BYTES:
        raise RequestError("Identity reference images must be 5 MB or smaller.", 413)
    try:
        with Image.open(BytesIO(content)) as source:
            source.verify()
        with Image.open(BytesIO(content)) as source:
            width, height = source.size
            if width < 64 or height < 64:
                raise RequestError("Identity reference images must be at least 64 by 64 pixels.", 400)
            if width * height > MAX_IMAGE_PIXELS:
                raise RequestError("Identity reference images exceed the safe pixel limit.", 400)
            source.load()
            image = source.convert("RGB")
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
            normalized = output.getvalue()
    except RequestError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise RequestError("The identity reference is not a supported, safe image.", 400) from exc
    if len(normalized) > MAX_REFERENCE_BYTES:
        raise RequestError("The normalized identity image exceeds the verifier's 5 MB limit.", 413)
    return NormalizedIdentityImage(normalized, image.width, image.height, sha256(normalized).hexdigest())
