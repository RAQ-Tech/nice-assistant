"""Conversation context for capability planning.

Planning used to see only the current message, so a request that referred to
something established earlier could not be routed or described correctly. This
widens the window over what the *user* said. ADR 0017's exclusion of persona
reply prose is unchanged and is pinned here, because that exclusion is what
stops a persona inventing or widening a media subject.
"""

import json
from pathlib import Path
import tempfile
import unittest

from app.capability_service import (
    PLANNING_CONTEXT_CHARACTERS,
    PLANNING_CONTEXT_MESSAGES,
)
from app.provider_contracts import MediaArtifact
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


class FakeImageProvider:
    name = "local-image"

    def generate(self, _request, cancellation):
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


def planning_payloads(provider: FakeChatProvider) -> list[dict]:
    payloads = []
    for request in provider.task_requests:
        if provider._task_role(request) != CAPABILITY_PLANNING:
            continue
        for message in request.messages:
            if message.get("role") == "user":
                payloads.append(json.loads(message["content"]))
    return payloads


class PlanningContextTests(unittest.TestCase):
    def _planned(self, prompt: str) -> dict:
        return {
            "capability_key": "media.generate_image",
            "prompt": prompt,
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": False,
        }

    def _running(self, tmp, provider):
        return TestApp(Path(tmp), chat_provider=provider)

    def _turn(self, running, chat_id: str, text: str) -> dict:
        """Post a turn and wait for the follow-up job that runs planning."""

        accepted = running.client.post(
            f"/api/v1/chats/{chat_id}/turns",
            json={"text": text, "memory_mode": "off"},
        ).json()
        chat_job = running.wait_job(accepted["job"]["id"])
        followup = (chat_job.get("result") or {}).get("followup_job_id")
        if followup:
            running.wait_job(followup)
        return accepted

    def _ready(self, running):
        """Capability planning only runs when a capability is actually available."""

        running.create_and_login()
        running.services.providers.media_providers["local-image"] = FakeImageProvider()
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )

    def test_planning_sees_earlier_user_messages_so_a_reference_resolves(self):
        provider = FakeChatProvider(
            ["Sure."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [self._planned("a pastel manicure")]}},
        )
        with tempfile.TemporaryDirectory() as tmp, self._running(tmp, provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "Colours", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "I love soft pastel colours, especially lilac")
            self._turn(running, chat["id"], "Show me a picture of nails in those colours")

            payloads = planning_payloads(provider)
            self.assertTrue(payloads)
            latest = payloads[-1]
            self.assertIn("recent_user_messages", latest)
            self.assertIn(
                "I love soft pastel colours, especially lilac",
                latest["recent_user_messages"],
            )
            # The current request is still the authoritative one.
            self.assertIn("nails", latest["user_text"])

    def test_persona_reply_prose_still_never_reaches_planning(self):
        provider = FakeChatProvider(
            ["Of course - here is a lovely portrait of me on a beach in a red dress."],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, self._running(tmp, provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "Chat", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "How was your day?")
            self._turn(running, chat["id"], "That sounds nice")

            for payload in planning_payloads(provider):
                serialized = json.dumps(payload)
                # ADR 0017: persona prose must never be able to introduce or
                # widen a media subject.
                self.assertNotIn("red dress", serialized)
                self.assertNotIn("beach", serialized)

    def test_the_window_is_bounded_in_count_and_size(self):
        provider = FakeChatProvider(
            ["Noted."],
            task_outputs={CAPABILITY_PLANNING: {"requests": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, self._running(tmp, provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "Long", "memory_mode": "off"}).json()
            for index in range(PLANNING_CONTEXT_MESSAGES + 4):
                self._turn(running, chat["id"], f"message number {index} " + ("padding " * 60))

            window = planning_payloads(provider)[-1]["recent_user_messages"]
            self.assertLessEqual(len(window), PLANNING_CONTEXT_MESSAGES)
            self.assertLessEqual(sum(len(item) for item in window), PLANNING_CONTEXT_CHARACTERS)
            # Newest messages are the ones most likely to be referenced, so they
            # are the ones that survive a full budget.
            self.assertIn("message number", window[-1])

    def test_a_first_message_plans_with_an_empty_window(self):
        provider = FakeChatProvider(["Hi."], task_outputs={CAPABILITY_PLANNING: {"requests": []}})
        with tempfile.TemporaryDirectory() as tmp, self._running(tmp, provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "New", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "Hello there")
            window = planning_payloads(provider)[-1]["recent_user_messages"]
            self.assertEqual(window, ["Hello there"])

    def test_the_window_that_informed_a_picture_is_recorded_in_its_journal(self):
        provider = FakeChatProvider(
            ["Here you go."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [self._planned("a pastel manicure")]}},
        )
        with tempfile.TemporaryDirectory() as tmp, self._running(tmp, provider) as running:
            self._ready(running)
            chat = running.client.post("/api/v1/chats", json={"title": "Colours", "memory_mode": "off"}).json()
            self._turn(running, chat["id"], "I love soft pastel colours")
            self._turn(running, chat["id"], "Send me a picture of nails in those colours")
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertTrue(requests)
            job_id = requests[0]["job_id"]
            media_id = running.wait_job(job_id)["result"]["mediaId"]

            journal = running.client.get(f"/api/v1/media/{media_id}/journal").json()
            stage = next(item for item in journal["stages"] if item["stage"] == "request")
            self.assertIn(
                "I love soft pastel colours",
                " ".join(stage["detail"]["planning_context"]),
            )


if __name__ == "__main__":
    unittest.main()
