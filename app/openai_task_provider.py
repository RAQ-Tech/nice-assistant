"""OpenAI adapter for platform task roles.

The task contract is meant to be provider-neutral: strict JSON matching a supplied
schema, a token budget, a timeout, and a temperature. Ollama was the only implementation,
so nothing proved the contract was not quietly Ollama-shaped. This is the second one.

It is deliberately not registered as a persona chat provider. Task roles emit structured
data under an enforced schema; persona conversation is a separate product decision about
where replies are generated, and adding an adapter here should not silently make that
choice.
"""

from __future__ import annotations

import json
import urllib.error

from app.media_clients import openai_auth_json_request
from app.provider_contracts import ProviderError, ProviderHealth, ProviderStatus

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

# Models that accept a JSON schema response format. The task contract requires it, so a
# model without it cannot honour the role and is not advertised.
STRUCTURED_OUTPUT_MODELS = ("gpt-4o-mini", "gpt-4o")


class OpenAITaskModelProvider:
    """Runs a task role against OpenAI, using the same strict-schema contract."""

    name = "openai"

    def __init__(self, url: str = OPENAI_CHAT_COMPLETIONS_URL):
        self.url = url

    def list_models(self) -> list[str]:
        return list(STRUCTURED_OUTPUT_MODELS)

    def health(self) -> ProviderHealth:
        # Reachability is not checked here. A task run reports its own outcome, and a
        # readiness claim without a request behind it would not be evidence.
        return ProviderHealth(
            self.name,
            ProviderStatus.READY,
            "Configured. A task run reports the real result.",
            0,
        )

    def generate(self, request, cancellation) -> str:
        cancellation.raise_if_cancelled()
        api_key = str(request.options.get("api_key") or "").strip()
        if not api_key:
            raise ProviderError(
                provider=self.name,
                code="openai_api_key_missing",
                user_message="Add an OpenAI API key in Settings before using an OpenAI task model.",
            )
        payload = {
            "model": request.model,
            "messages": request.messages,
            "max_completion_tokens": int(request.options.get("num_predict") or 512),
            "temperature": float(request.options.get("temperature") or 0.0),
            "response_format": self._response_format(request.response_format),
        }
        try:
            body = openai_auth_json_request(
                self.url,
                payload,
                api_key,
                timeout=max(1, int(request.timeout_seconds or 60)),
            )
        except urllib.error.HTTPError as exc:
            raise ProviderError(
                provider=self.name,
                code="task_provider_unavailable",
                user_message="The OpenAI task model request failed.",
                retryable=exc.code >= 500 or exc.code == 429,
            ) from exc
        except Exception as exc:
            raise ProviderError(
                provider=self.name,
                code="task_provider_unavailable",
                user_message="The OpenAI task model could not be reached.",
                retryable=True,
            ) from exc
        cancellation.raise_if_cancelled()
        return self._content(body)

    @staticmethod
    def _response_format(schema) -> dict:
        """The role's schema, as OpenAI's strict structured-output envelope."""

        if not isinstance(schema, dict):
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "task_output", "strict": True, "schema": schema},
        }

    @staticmethod
    def _content(body) -> str:
        try:
            choices = body["choices"]
            message = choices[0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                provider="openai",
                code="invalid_task_response",
                user_message="The OpenAI task model returned an unexpected response shape.",
            ) from exc
        if message.get("refusal"):
            # A refusal is a real terminal outcome, not a malformed response.
            raise ProviderError(
                provider="openai",
                code="task_refused",
                user_message="The OpenAI task model declined this request.",
            )
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        text = str(content or "").strip()
        if not text:
            raise ProviderError(
                provider="openai",
                code="invalid_task_response",
                user_message="The OpenAI task model returned an empty result.",
            )
        return text

    def model_context(self, model: str):
        return None

    def stream(self, request, cancellation):
        raise ProviderError(
            provider=self.name,
            code="streaming_not_supported",
            user_message="This adapter runs platform task roles only, not conversation.",
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"OpenAITaskModelProvider(models={list(STRUCTURED_OUTPUT_MODELS)})"


def structured_output_payload(schema: dict) -> str:
    """Round-trippable rendering used by tests to build a valid fake response."""

    return json.dumps({"choices": [{"message": {"content": json.dumps(schema)}}]})
