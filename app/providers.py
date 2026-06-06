import json
import time
import urllib.error
import urllib.parse
import urllib.request

from app.auth import redact_sensitive_text


def provider_test_response(provider, ok, status, message, detail=""):
    return {
        "ok": bool(ok),
        "provider": provider,
        "status": status,
        "message": message,
        "detail": redact_sensitive_text(detail or "")[:1000],
        "checkedAt": int(time.time()),
    }


def provider_test_error_detail(exc):
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        pieces = [f"HTTP {exc.code}"]
        if exc.reason:
            pieces.append(str(exc.reason))
        if body:
            pieces.append(body[:500])
        return redact_sensitive_text(". ".join(pieces))
    if isinstance(exc, urllib.error.URLError):
        return redact_sensitive_text(f"Connection failed: {exc.reason}")
    return redact_sensitive_text(str(exc) or exc.__class__.__name__)


def normalize_provider_base_url(raw_url, default_url):
    candidate = (raw_url or "").strip() or default_url
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Provider URL must be a valid http(s) URL")
    return candidate.rstrip("/")


def provider_get_json(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return {}
    return json.loads(raw)


def voice_ids_from_payload(payload):
    if isinstance(payload, list):
        return sorted({str(v).strip() for v in payload if str(v).strip()})
    if isinstance(payload, dict):
        for key in ("voices", "data", "items"):
            values = payload.get(key)
            if not isinstance(values, list):
                continue
            voices = []
            for value in values:
                if isinstance(value, str):
                    voices.append(value.strip())
                elif isinstance(value, dict):
                    voices.append(str(value.get("id") or value.get("name") or "").strip())
            return sorted({voice for voice in voices if voice})
    return []
