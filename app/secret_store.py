import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


SECRET_PREFIX = "enc:v1:"


class SecretConfigurationError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, raw_key=None):
        raw_key = raw_key if raw_key is not None else os.getenv("NICE_ASSISTANT_MASTER_KEY", "")
        self._fernet = Fernet(self._normalize_key(raw_key)) if str(raw_key or "").strip() else None

    @staticmethod
    def _normalize_key(raw_key):
        raw = str(raw_key or "").strip().encode("utf-8")
        try:
            decoded = base64.urlsafe_b64decode(raw)
            if len(decoded) == 32:
                return raw
        except Exception:
            pass
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    @property
    def available(self):
        return self._fernet is not None

    def encrypt(self, value):
        if not value:
            return None
        if not self._fernet:
            raise SecretConfigurationError("NICE_ASSISTANT_MASTER_KEY is required before saving provider secrets")
        return SECRET_PREFIX + self._fernet.encrypt(str(value).encode("utf-8")).decode("ascii")

    def decrypt(self, value):
        if not value:
            return ""
        raw = str(value)
        if not raw.startswith(SECRET_PREFIX):
            return raw
        if not self._fernet:
            raise SecretConfigurationError("NICE_ASSISTANT_MASTER_KEY is required to decrypt provider secrets")
        try:
            return self._fernet.decrypt(raw[len(SECRET_PREFIX) :].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretConfigurationError(
                "NICE_ASSISTANT_MASTER_KEY does not match the stored provider secrets"
            ) from exc


SECRET_STORE = SecretStore()
