import base64
import hashlib
import hmac
import re
import secrets


PASSWORD_SCHEME = "pbkdf2_sha256"
# OWASP's figure for PBKDF2-HMAC-SHA256. About 0.13 s per sign-in on a desktop
# CPU, which a sixty-day session pays rarely and a password guesser pays on
# every guess.
PASSWORD_ITERATIONS = 600_000
# Every hash written before 2026-10-29: `salt$digest`, with the count implied.
LEGACY_PASSWORD_ITERATIONS = 120_000
# A stored count above this is damage, not strength, and would stall a sign-in.
MAX_PASSWORD_ITERATIONS = 10_000_000
_LEGACY_SALT = re.compile(r"[0-9a-f]{32}")


def _derive(password: str, salt: str, iterations: int) -> str:
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)).decode()


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$iterations$salt$digest`: the count travels with the hash, so it can rise later."""

    salt = secrets.token_hex(16)
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt}${_derive(password, salt, PASSWORD_ITERATIONS)}"


def _parse_password_hash(stored: str) -> tuple[int, str, str] | None:
    """Iterations, salt and digest of either stored form, or None for anything else."""

    parts = str(stored or "").split("$")
    if len(parts) == 4 and parts[0] == PASSWORD_SCHEME and parts[1].isdigit():
        iterations = int(parts[1])
        return (iterations, parts[2], parts[3]) if 1 <= iterations <= MAX_PASSWORD_ITERATIONS else None
    if len(parts) == 2 and _LEGACY_SALT.fullmatch(parts[0]):
        return LEGACY_PASSWORD_ITERATIONS, parts[0], parts[1]
    return None


def verify_password(password: str, stored: str) -> bool:
    parsed = _parse_password_hash(stored)
    if parsed is None:
        return False
    iterations, salt, digest = parsed
    # Constant time, so how long a wrong guess takes says nothing about how close it was.
    return hmac.compare_digest(_derive(password, salt, iterations), digest)


def password_needs_rehash(stored: str) -> bool:
    """Whether a hash that just verified was made more weakly than one made now.

    Only a hash this module recognises is ever upgraded; anything else could not
    have verified in the first place.
    """

    parsed = _parse_password_hash(stored)
    if parsed is None:
        return False
    return not str(stored).startswith(f"{PASSWORD_SCHEME}$") or parsed[0] < PASSWORD_ITERATIONS


def mask_secret(value):
    raw = str(value or "")
    if not raw:
        return ""
    return f"********{raw[-4:]}"


def is_masked_secret(value):
    raw = str(value or "")
    return raw.startswith("********")


def redact_sensitive_text(text):
    redacted = str(text or "")
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(basic\s+)[A-Za-z0-9+/=]{8,}", r"\1[REDACTED]", redacted)
    redacted = re.sub(r"(?i)(https?://[^/\s:@]+:)[^@\s/]+@", r"\1[REDACTED]@", redacted)
    labeled_secret_patterns = [
        r"(?i)((?:\"?openai_api_key\"?|\"?image_local_api_auth\"?|\"?api_auth\"?|\"?authorization\"?)\s*[=:]\s*\"?)([^\"\s,;}]+)",
    ]
    for pattern in labeled_secret_patterns:
        redacted = re.sub(pattern, r"\1[REDACTED]", redacted)
    return redacted
