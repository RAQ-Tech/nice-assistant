import base64
import hashlib
import re
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
    return f"{salt}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000)
        return base64.b64encode(dk).decode() == digest
    except Exception:
        return False


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
