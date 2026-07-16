from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from fastapi.testclient import TestClient

from app.asgi import create_app
from app.chat import generate_chat_title_from_first_user_message
from app.provider_contracts import (
    ChatDelta,
    ChatToolCall,
    ModelContextProfile,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
)
from app.provider_registry import ProviderRegistry
from app.runtime import AppConfig
from app.secret_store import SecretStore
from app.task_contracts import CAPABILITY_PLANNING, CONVERSATION_SUMMARY, MEMORY_EXTRACTION, TITLE_GENERATION


def fast_hash(password):
    return f"test-only${password}"


def fast_verify(password, stored):
    return stored == fast_hash(password)


class FakeChatProvider:
    name = "ollama"

    def __init__(
        self,
        chunks=None,
        *,
        error: ProviderError | None = None,
        gate: threading.Event | None = None,
        memory_candidates: list[dict] | None = None,
        memory_gate: threading.Event | None = None,
        tool_calls: list[ChatToolCall] | None = None,
        task_outputs: dict[str, object] | None = None,
        task_errors: dict[str, ProviderError] | None = None,
    ):
        self.chunks = list(chunks or ["Test reply."])
        self.error = error
        self.gate = gate
        self.requests = []
        self.task_requests = []
        self.memory_requests = []
        self.memory_candidates = list(memory_candidates or [])
        self.memory_gate = memory_gate
        self.tool_calls = list(tool_calls or [])
        self.task_outputs = dict(task_outputs or {})
        self.task_errors = dict(task_errors or {})
        self.memory_started = threading.Event()
        self.started = threading.Event()

    def list_models(self):
        return ["fake-model"]

    def health(self):
        return ProviderHealth("ollama", ProviderStatus.READY, "Fake Ollama is ready.", 1)

    def model_context(self, model):
        return ModelContextProfile("ollama", model, 8192, "fake")

    def stream(self, request, cancellation):
        self.requests.append(request)
        self.started.set()
        if self.gate:
            while not self.gate.wait(0.01):
                cancellation.raise_if_cancelled()
        if self.error:
            raise self.error
        for index, chunk in enumerate(self.chunks):
            cancellation.raise_if_cancelled()
            done = index == len(self.chunks) - 1
            yield ChatDelta(chunk, done=done, tool_calls=self.tool_calls if done else [])

    def generate(self, request, cancellation):
        role = self._task_role(request)
        if role:
            cancellation.raise_if_cancelled()
            self.task_requests.append(request)
            if role in self.task_errors:
                raise self.task_errors[role]
            if role == MEMORY_EXTRACTION:
                self.memory_requests.append(request)
                self.memory_started.set()
                if self.memory_gate:
                    while not self.memory_gate.wait(0.01):
                        cancellation.raise_if_cancelled()
            if role in self.task_outputs:
                value = self.task_outputs[role]
                return value if isinstance(value, str) else json.dumps(value)
            payload = json.loads(request.messages[-1].get("content") or "{}")
            if role == TITLE_GENERATION:
                return json.dumps(
                    {"title": generate_chat_title_from_first_user_message(payload.get("user_text") or "", max_len=80)}
                )
            if role == CONVERSATION_SUMMARY:
                return json.dumps({"summary": "compact summary"})
            if role == MEMORY_EXTRACTION:
                return json.dumps({"candidates": self.memory_candidates})
            if role == CAPABILITY_PLANNING:
                requests = []
                tool_to_key = {"generate_image": "media.generate_image", "generate_video": "media.generate_video"}
                for call in self.tool_calls:
                    key = tool_to_key.get(call.name)
                    if key:
                        requests.append(
                            {
                                "capability_key": key,
                                "prompt": call.arguments.get("prompt", ""),
                                "operation": "generate",
                                "domains": [],
                                "content_tags": [],
                                "required_features": [],
                                "persona_subject": False,
                            }
                        )
                return json.dumps({"requests": requests})
        return "".join(delta.text for delta in self.stream(request, cancellation))

    @staticmethod
    def _task_role(request):
        if not request.messages:
            return None
        marker = "Nice Assistant platform task: "
        content = request.messages[0].get("content", "")
        if not content.startswith(marker):
            return None
        return content[len(marker) :].split(".", 1)[0]


class TestApp:
    def __init__(
        self,
        base: Path,
        *,
        chat_provider=None,
        json_limit=1024 * 1024,
        interactive_workers=1,
        identity_providers=None,
        resource_providers=None,
    ):
        self.base = base
        self.chat_provider = chat_provider or FakeChatProvider()
        self.config = AppConfig(
            data_dir=base / "data",
            archive_dir=base / "archive",
            allow_public_signup=True,
            max_json_body_bytes=json_limit,
            max_upload_body_bytes=max(json_limit, 2 * 1024 * 1024),
            interactive_workers=interactive_workers,
        )
        self.app = create_app(
            self.config,
            secret_store=SecretStore("nice-assistant-test-key"),
            providers=ProviderRegistry(chat_providers={"ollama": self.chat_provider}),
            identity_providers=identity_providers,
            resource_providers=resource_providers,
            password_hasher=fast_hash,
            password_verifier=fast_verify,
        )
        self.context = TestClient(self.app)
        self.client = None

    def __enter__(self):
        self.client = self.context.__enter__()
        self.client.headers.update({"X-Nice-Assistant-CSRF": "1"})
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self.context.__exit__(exc_type, exc, traceback)

    @property
    def services(self):
        return self.app.state.services

    def create_and_login(self, username="owner"):
        credentials = {"username": username, "password": "pass1234"}
        created = self.client.post("/api/v1/users", json=credentials)
        assert created.status_code == 200, created.text
        logged_in = self.client.post("/api/v1/session", json=credentials)
        assert logged_in.status_code == 200, logged_in.text
        return logged_in.json().get("user_id") or created.json().get("id")

    def wait_job(self, job_id: str, *, timeout=5):
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            response = self.client.get("/api/v1/jobs/" + job_id)
            assert response.status_code == 200, response.text
            latest = response.json()
            if latest["status"] in {"completed", "failed", "cancelled"}:
                return latest
            time.sleep(0.01)
        raise AssertionError(f"job did not finish: {latest}")
