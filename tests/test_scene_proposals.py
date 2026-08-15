"""Proposing scenes from what a persona already is.

The ideas come from the persona's own card, its lorebook, and what recent
conversations were about. Every proposal records which of those suggested it, so
it can be judged rather than accepted on instinct. Nothing is auto-approved. See
ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.task_contracts import SCENE_PROPOSAL, SceneProposalTaskInput, TaskContractError, task_definition
from tests.support import FakeChatProvider, TestApp


def proposal(subject: str, source: str = "persona_card", detail: str = "likes rainy walks") -> dict:
    return {
        "scene": {
            "subject": subject,
            "action": "walking",
            "setting": "a wet street",
            "wardrobe": "",
            "framing": "",
            "lighting": "",
            "camera": "",
            "mood": "",
        },
        "source": source,
        "source_detail": detail,
    }


class SceneProposalContractTests(unittest.TestCase):
    def _input(self, limit: int = 3) -> SceneProposalTaskInput:
        return SceneProposalTaskInput(persona_name="Avery", card="likes rainy walks", limit=limit)

    def test_the_model_is_asked_for_a_scene_and_its_provenance(self):
        definition = task_definition(SCENE_PROPOSAL)
        item = definition.response_schema(self._input())["properties"]["proposals"]["items"]
        self.assertEqual(set(item["required"]), {"scene", "source", "source_detail"})
        self.assertEqual(item["properties"]["source"]["enum"], ["persona_card", "lorebook", "conversation"])

    def test_the_prompt_forbids_naming_resources(self):
        content = task_definition(SCENE_PROPOSAL).messages(self._input())[0]["content"]
        self.assertIn("never name a provider", content)
        self.assertIn("already_proposed_or_made", content)

    def test_a_proposal_with_an_empty_scene_is_refused(self):
        definition = task_definition(SCENE_PROPOSAL)
        empty = proposal("")
        empty["scene"] = {key: "" for key in empty["scene"]}
        import json as _json

        with self.assertRaises(TaskContractError):
            definition.parse_output(_json.dumps({"proposals": [empty]}), self._input(), 768)

    def test_an_unknown_source_is_refused(self):
        import json as _json

        definition = task_definition(SCENE_PROPOSAL)
        with self.assertRaises(TaskContractError):
            definition.parse_output(
                _json.dumps({"proposals": [proposal("avery", source="a hunch")]}),
                self._input(),
                768,
            )

    def test_duplicate_ideas_are_collapsed_and_the_limit_is_honored(self):
        import json as _json

        definition = task_definition(SCENE_PROPOSAL)
        parsed = definition.parse_output(
            _json.dumps({"proposals": [proposal("avery"), proposal("avery"), proposal("roofus")]}),
            self._input(limit=3),
            768,
        )
        self.assertEqual([item.scene["subject"] for item in parsed.proposals], ["avery", "roofus"])


class SceneProposalServiceTests(unittest.TestCase):
    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        persona = running.client.post(
            "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}
        ).json()
        running.client.put(
            f"/api/v1/personas/{persona['id']}/card",
            json={"card_definition": "Avery likes rainy walks with her dog Roofus."},
        )
        return persona

    def test_proposals_land_in_the_backlog_with_their_provenance(self):
        provider = FakeChatProvider(
            ["ok"],
            task_outputs={
                SCENE_PROPOSAL: {
                    "proposals": [
                        proposal("avery", source="persona_card", detail="likes rainy walks"),
                        proposal("roofus", source="lorebook", detail="Roofus the dog"),
                    ]
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            persona = self._persona(running)

            result = running.client.post(
                "/api/v1/scene-backlog/proposals", json={"persona_id": persona["id"], "limit": 2}
            )
            self.assertEqual(result.status_code, 200, result.text)
            proposed = result.json()["proposed"]
            self.assertEqual(len(proposed), 2)
            self.assertEqual({item["source"] for item in proposed}, {"persona_card", "lorebook"})
            self.assertEqual({item["state"] for item in proposed}, {"proposed"})
            self.assertTrue(result.json()["model_answered"])
            # Nothing is approved, so nothing can reach generation without a
            # person agreeing to it.
            self.assertTrue(all(item["source_detail"] for item in proposed))

    def test_the_persona_card_and_existing_ideas_are_given_to_the_model(self):
        provider = FakeChatProvider(["ok"], task_outputs={SCENE_PROPOSAL: {"proposals": [proposal("avery")]}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            persona = self._persona(running)
            running.client.post("/api/v1/scene-backlog/proposals", json={"persona_id": persona["id"]})
            running.client.post("/api/v1/scene-backlog/proposals", json={"persona_id": persona["id"]})

            import json as _json

            payloads = [
                _json.loads(message["content"])
                for request in provider.task_requests
                if provider._task_role(request) == SCENE_PROPOSAL
                for message in request.messages
                if message.get("role") == "user"
            ]
            self.assertIn("rainy walks", payloads[0]["persona_card"])
            # The second call knows what was already proposed, so it does not
            # simply restate it.
            self.assertTrue(payloads[1]["already_proposed_or_made"])

    def test_a_model_that_did_not_answer_is_reported_rather_than_silently_empty(self):
        provider = FakeChatProvider(["ok"], task_outputs={SCENE_PROPOSAL: {"proposals": "not a list"}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            persona = self._persona(running)
            result = running.client.post("/api/v1/scene-backlog/proposals", json={"persona_id": persona["id"]})

            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["proposed"], [])
            # "The model did not answer" and "the model had no ideas" need
            # different fixes, so they must not look the same.
            self.assertFalse(result.json()["model_answered"])

    def test_proposals_require_a_persona_the_owner_has(self):
        provider = FakeChatProvider(["ok"], task_outputs={SCENE_PROPOSAL: {"proposals": []}})
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            self.assertEqual(
                running.client.post("/api/v1/scene-backlog/proposals", json={"persona_id": "nope"}).status_code,
                404,
            )


if __name__ == "__main__":
    unittest.main()
