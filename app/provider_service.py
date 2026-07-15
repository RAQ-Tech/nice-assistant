from __future__ import annotations

import base64
import urllib.error
import urllib.request

from app.auth import is_masked_secret
from app.providers import (
    normalize_provider_base_url,
    provider_get_json,
    provider_test_error_detail,
    provider_test_response,
    voice_ids_from_payload,
)
from app.repositories import UnitOfWork


def basic_auth_headers(value: str | None) -> dict:
    raw = str(value or "").strip()
    if not raw:
        return {}
    return {"Authorization": f"Basic {base64.b64encode(raw.encode()).decode('ascii')}"}


class ProviderService:
    def __init__(self, session_factory, secret_store, config, registry, logger, provider_url_policy=None):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.registry = registry
        self.logger = logger
        self.provider_url_policy = provider_url_policy

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def models(self) -> list[str]:
        return self.registry.models()

    def check(self, user_id: str, provider: str, overrides: dict | None = None) -> dict | None:
        provider = str(provider or "").strip().lower()
        if provider == "a1111":
            provider = "automatic1111"
        if provider not in {"ollama", "openai", "kokoro", "automatic1111", "comfyui"}:
            return None
        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {"preferences": {}}
        effective = {**settings, **(settings.get("preferences") or {})}
        incoming = overrides or {}
        preferences = incoming.get("preferences")
        if isinstance(preferences, dict):
            effective.update(preferences)
        effective.update({key: value for key, value in incoming.items() if key != "preferences"})
        key = incoming.get("openai_api_key")
        if key and not is_masked_secret(key):
            effective["openai_api_key"] = key
        label = {
            "ollama": "Ollama",
            "openai": "OpenAI",
            "kokoro": "Kokoro",
            "automatic1111": "Automatic1111",
            "comfyui": "ComfyUI",
        }[provider]
        try:
            if provider == "ollama":
                health = self.registry.chat("ollama").health()
                result = provider_test_response(
                    provider,
                    health.ok,
                    health.status.value,
                    health.message,
                    health.detail,
                )
            elif provider == "openai":
                api_key = str(effective.get("openai_api_key") or "").strip()
                if not api_key or is_masked_secret(api_key):
                    return provider_test_response(provider, False, "missing", "OpenAI API key is not configured.")
                request = urllib.request.Request(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=self.config.provider_timeout_seconds) as response:
                    import json

                    payload = json.loads(response.read().decode())
                models = payload.get("data", []) if isinstance(payload, dict) else []
                result = provider_test_response(
                    provider,
                    True,
                    "ready",
                    "OpenAI is reachable.",
                    f"{len(models)} model(s) visible.",
                )
            elif provider == "kokoro":
                base = normalize_provider_base_url(
                    effective.get("tts_local_base_url"),
                    "http://127.0.0.1:8880",
                )
                if self.provider_url_policy:
                    base = self.provider_url_policy.normalize(base, label="Kokoro")
                payload = provider_get_json(
                    f"{base}/v1/audio/voices",
                    timeout=self.config.provider_timeout_seconds,
                )
                voices = voice_ids_from_payload(payload)
                result = provider_test_response(
                    provider,
                    True,
                    "ready",
                    "Kokoro is reachable.",
                    f"{len(voices)} voice(s) available.",
                )
            else:
                default = (
                    self.config.automatic1111_base_url if provider == "automatic1111" else self.config.comfyui_base_url
                )
                base = normalize_provider_base_url(effective.get("image_local_base_url"), default)
                if self.provider_url_policy:
                    base = self.provider_url_policy.normalize(base, label=label)
                endpoint = "/sdapi/v1/options" if provider == "automatic1111" else "/system_stats"
                provider_get_json(
                    f"{base}{endpoint}",
                    headers=basic_auth_headers(effective.get("image_local_api_auth")),
                    timeout=self.config.provider_timeout_seconds,
                )
                result = provider_test_response(provider, True, "ready", f"{label} is reachable.")
        except ValueError as exc:
            result = provider_test_response(provider, False, "invalid", f"{label} configuration is invalid.", str(exc))
        except urllib.error.HTTPError as exc:
            result = provider_test_response(
                provider,
                False,
                "failed",
                f"{label} responded with an error.",
                provider_test_error_detail(exc),
            )
        except urllib.error.URLError as exc:
            result = provider_test_response(
                provider,
                False,
                "unreachable",
                f"{label} is not reachable.",
                provider_test_error_detail(exc),
            )
        except Exception as exc:  # noqa: BLE001 - safe readiness diagnostics
            result = provider_test_response(
                provider,
                False,
                "error",
                f"{label} test failed.",
                provider_test_error_detail(exc),
            )
        self.logger.info("provider readiness provider=%s status=%s", provider, result.get("status"))
        return result
