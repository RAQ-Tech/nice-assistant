from __future__ import annotations

from types import SimpleNamespace
import threading
import tempfile
import time
import unittest
from pathlib import Path

from app.chat_binding import resolve_chat_binding
from app.job_service import JobExecution
from app.task_contracts import CAPABILITY_PLANNING, MEMORY_EXTRACTION, TITLE_GENERATION
from tests.support import FakeChatProvider, TestApp


class _BindingRepository:
    def __init__(self, *, binding=None, human=None, persona=None, workspace=None, workspace_ids=()):
        self.binding = binding
        self.human = human
        self.persona_row = persona
        self.workspace_row = workspace
        self.workspace_ids = list(workspace_ids)

    def chat_binding(self, _chat_id):
        return self.binding

    def human_principal(self, _user_id):
        return self.human

    def persona(self, _user_id, persona_id):
        return self.persona_row if self.persona_row and self.persona_row.id == persona_id else None

    def workspace(self, _user_id, workspace_id):
        return self.workspace_row if self.workspace_row and self.workspace_row.id == workspace_id else None

    def persona_workspace_ids(self, _persona_id):
        return self.workspace_ids


class ChatBindingResolverTests(unittest.TestCase):
    def test_legacy_chat_is_readable_but_not_continuable(self):
        chat = SimpleNamespace(id="chat", persona_id="observed-persona", workspace_id="observed-workspace")

        resolved = resolve_chat_binding(_BindingRepository(), "owner", chat)

        self.assertEqual(resolved.binding_status, "legacy_unresolved")
        self.assertEqual(resolved.persona_id, "observed-persona")
        self.assertEqual(resolved.workspace_id, "observed-workspace")
        self.assertFalse(resolved.can_continue)
        self.assertEqual(resolved.block_code, "legacy_binding_unresolved")

    def test_workspace_binding_requires_current_membership_but_keeps_snapshot_labels(self):
        binding = SimpleNamespace(
            human_id="human",
            persona_id="persona",
            context_kind="workspace",
            workspace_id="workspace",
            binding_status="active",
            persona_name_snapshot="Historical Persona",
            workspace_name_snapshot="Historical Workspace",
        )
        repository = _BindingRepository(
            binding=binding,
            human=SimpleNamespace(id="human"),
            persona=SimpleNamespace(id="persona", name="Current Persona"),
            workspace=SimpleNamespace(id="workspace", name="Current Workspace"),
            workspace_ids=(),
        )

        resolved = resolve_chat_binding(repository, "owner", SimpleNamespace(id="chat"))

        self.assertFalse(resolved.can_continue)
        self.assertEqual(resolved.block_code, "persona_not_in_workspace")
        self.assertEqual(resolved.persona_name, "Current Persona")
        self.assertEqual(resolved.workspace_name, "Current Workspace")


class ChatBindingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = FakeChatProvider(["Bound reply."])
        self.test_app = TestApp(Path(self.tmp.name), chat_provider=self.provider)
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()
        self.first_workspace = self.client.post("/api/v1/workspaces", json={"name": "Private"}).json()
        self.second_workspace = self.client.post("/api/v1/workspaces", json={"name": "Studio"}).json()
        self.first_persona = self.client.post(
            "/api/v1/personas",
            json={
                "workspace_id": self.first_workspace["id"],
                "workspace_ids": [self.first_workspace["id"], self.second_workspace["id"]],
                "name": "Avery",
                "system_prompt": "Use BOUND-AVERY.",
            },
        ).json()
        self.second_persona = self.client.post(
            "/api/v1/personas",
            json={
                "workspace_id": self.first_workspace["id"],
                "workspace_ids": [self.first_workspace["id"]],
                "name": "Robin",
                "system_prompt": "Use BOUND-ROBIN.",
            },
        ).json()

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _chat(self, *, context=None, persona_id=None):
        response = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": persona_id or self.first_persona["id"],
                "access_context": context or {"kind": "workspace", "workspace_id": self.first_workspace["id"]},
                "title": "Bound chat",
                "memory_mode": "off",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_chat_creation_does_not_fall_back_to_a_persona(self):
        response = self.client.post(
            "/api/v1/chats",
            json={"access_context": {"kind": "personal"}, "title": "Missing persona"},
        )

        self.assertEqual(response.status_code, 422, response.text)

    def test_chat_creation_requires_an_explicit_well_shaped_access_context(self):
        missing = self.client.post(
            "/api/v1/chats",
            json={"persona_id": self.first_persona["id"], "title": "Implicit personal is forbidden"},
        )
        self.assertEqual(missing.status_code, 422, missing.text)

        legacy = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": self.first_persona["id"],
                "workspace_id": self.first_workspace["id"],
                "title": "Legacy workspace input is forbidden",
            },
        )
        self.assertEqual(legacy.status_code, 422, legacy.text)

        conflicting = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": self.first_persona["id"],
                "access_context": {"kind": "personal"},
                "workspace_id": self.first_workspace["id"],
            },
        )
        self.assertEqual(conflicting.status_code, 422, conflicting.text)

        personal_with_workspace = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": self.first_persona["id"],
                "access_context": {
                    "kind": "personal",
                    "workspace_id": self.first_workspace["id"],
                },
            },
        )
        self.assertEqual(personal_with_workspace.status_code, 422, personal_with_workspace.text)

        workspace_without_id = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": self.first_persona["id"],
                "access_context": {"kind": "workspace"},
            },
        )
        self.assertEqual(workspace_without_id.status_code, 422, workspace_without_id.text)

    def test_personal_and_workspace_contexts_have_canonical_binding_responses(self):
        personal = self._chat(context={"kind": "personal"})
        workspace = self._chat()

        self.assertIsNone(personal["workspace_id"])
        self.assertEqual(personal["persona_id"], self.first_persona["id"])
        self.assertEqual(personal["binding"]["context"]["kind"], "personal")
        self.assertTrue(personal["binding"]["can_continue"])
        self.assertEqual(workspace["workspace_id"], self.first_workspace["id"])
        self.assertEqual(workspace["binding"]["context"]["workspace_id"], self.first_workspace["id"])

        renamed_persona = {**self.first_persona, "name": "Avery Renamed"}
        renamed_persona.pop("id", None)
        renamed_persona.pop("created_at", None)
        saved_persona = self.client.put(
            f"/api/v1/personas/{self.first_persona['id']}",
            json=renamed_persona,
        )
        self.assertEqual(saved_persona.status_code, 200, saved_persona.text)
        renamed_workspace = self.client.put(
            f"/api/v1/workspaces/{self.first_workspace['id']}",
            json={"name": "Private Renamed"},
        )
        self.assertEqual(renamed_workspace.status_code, 200, renamed_workspace.text)

        current = self.client.get(f"/api/v1/chats/{workspace['id']}").json()["chat"]
        self.assertEqual(current["binding"]["persona_id"], self.first_persona["id"])
        self.assertEqual(current["binding"]["persona_name"], "Avery Renamed")
        self.assertEqual(current["binding"]["context"]["workspace_id"], self.first_workspace["id"])
        self.assertEqual(current["binding"]["context"]["workspace_name"], "Private Renamed")
        self.assertTrue(current["binding"]["can_continue"])

    def test_update_and_turn_cannot_rebind_the_chat(self):
        chat = self._chat()
        switched = self.client.put(
            f"/api/v1/chats/{chat['id']}",
            json={"persona_id": self.second_persona["id"]},
        )
        self.assertEqual(switched.status_code, 409, switched.text)
        identical = self.client.put(
            f"/api/v1/chats/{chat['id']}",
            json={"persona_id": self.first_persona["id"], "title": "Still Avery"},
        )
        self.assertEqual(identical.status_code, 200, identical.text)

        wrong_persona = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Do not persist this.", "persona_id": self.second_persona["id"], "memory_mode": "off"},
        )
        self.assertEqual(wrong_persona.status_code, 409, wrong_persona.text)
        wrong_workspace = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Do not persist this either.", "workspace_id": self.second_workspace["id"]},
        )
        self.assertEqual(wrong_workspace.status_code, 409, wrong_workspace.text)
        detail = self.client.get(f"/api/v1/chats/{chat['id']}").json()
        self.assertEqual(detail["messages"], [])
        self.assertEqual(self.provider.requests, [])

        accepted = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={
                "text": "This uses the unchanged binding.",
                "persona_id": self.first_persona["id"],
                "workspace_id": self.first_workspace["id"],
                "memory_mode": "off",
            },
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(self.running.wait_job(accepted.json()["job"]["id"])["status"], "completed")
        prompt = "\n".join(message["content"] for message in self.provider.requests[-1].messages)
        self.assertIn("BOUND-AVERY", prompt)
        self.assertNotIn("BOUND-ROBIN", prompt)

    def test_membership_removal_makes_history_readable_and_blocks_new_turns(self):
        chat = self._chat()
        updated = self.client.put(
            f"/api/v1/personas/{self.first_persona['id']}",
            json={
                "workspace_id": self.second_workspace["id"],
                "workspace_ids": [self.second_workspace["id"]],
                "name": self.first_persona["name"],
                "system_prompt": self.first_persona["system_prompt"],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        detail = self.client.get(f"/api/v1/chats/{chat['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertFalse(detail.json()["chat"]["binding"]["can_continue"])
        self.assertEqual(detail.json()["chat"]["binding"]["block_code"], "persona_not_in_workspace")
        blocked = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "This chat is now read-only.", "memory_mode": "off"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"], [])
        self.assertEqual(self.provider.requests, [])

    def test_membership_removal_blocks_new_image_edits_before_work_is_created(self):
        chat = self._chat()
        updated = self.client.put(
            f"/api/v1/personas/{self.first_persona['id']}",
            json={
                "workspace_id": self.second_workspace["id"],
                "workspace_ids": [self.second_workspace["id"]],
                "name": self.first_persona["name"],
                "system_prompt": self.first_persona["system_prompt"],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        blocked = self.client.post(
            "/api/v1/media/image-edit-jobs",
            json={
                "prompt": "Change the jacket to blue.",
                "operation": "image_to_image",
                "source_media_id": "source-image",
                "chat_id": chat["id"],
            },
        )

        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("no longer available", blocked.json()["error"]["message"].lower())
        requests = self.client.get(
            "/api/v1/capability-requests",
            params={"chat_id": chat["id"]},
        ).json()["items"]
        self.assertEqual(requests, [])

    def test_queued_turn_rechecks_binding_before_calling_the_provider(self):
        gate = threading.Event()
        self.provider.gate = gate
        occupying_chat = self._chat()
        queued_chat = self._chat(persona_id=self.second_persona["id"])
        occupying = self.client.post(
            f"/api/v1/chats/{occupying_chat['id']}/turns",
            json={"text": "Keep the interactive worker occupied.", "memory_mode": "off"},
        )
        self.assertEqual(occupying.status_code, 202, occupying.text)
        self.assertTrue(self.provider.started.wait(1))
        queued = self.client.post(
            f"/api/v1/chats/{queued_chat['id']}/turns",
            json={"text": "Recheck before this reaches the model.", "memory_mode": "off"},
        )
        self.assertEqual(queued.status_code, 202, queued.text)

        removed = self.client.put(
            f"/api/v1/personas/{self.second_persona['id']}",
            json={
                "workspace_id": self.second_workspace["id"],
                "workspace_ids": [self.second_workspace["id"]],
                "name": self.second_persona["name"],
                "system_prompt": self.second_persona["system_prompt"],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        gate.set()

        self.assertEqual(self.running.wait_job(occupying.json()["job"]["id"])["status"], "completed")
        failed = self.running.wait_job(queued.json()["job"]["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("no longer available", failed["error"].lower())
        self.assertEqual(len(self.provider.requests), 1)

    def test_queued_background_models_recheck_binding_before_provider_calls(self):
        self.running.services.providers.media_providers["local-image"] = object()
        saved = self.client.put(
            "/api/v1/settings",
            json={
                "preferences": {
                    "image_provider": "local/automatic1111",
                    "image_confirmation_policy": "always_ask",
                }
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        chat = self._chat()
        renamed = self.client.put(f"/api/v1/chats/{chat['id']}", json={"title": "New chat"})
        self.assertEqual(renamed.status_code, 200, renamed.text)

        blocker_started = threading.Event()
        blocker_release = threading.Event()
        with self.running.services.jobs._uow() as uow:
            blocker = uow.repo.add_job(
                user_id=self.running.current_user_id,
                chat_id=None,
                turn_id=None,
                kind="image",
                progress="Queued",
            )

        def block_media_lane(token):
            blocker_started.set()
            while not blocker_release.wait(0.01):
                token.raise_if_cancelled()
            return {"ok": True}

        self.running.services.jobs.submit(
            job_id=blocker.id,
            job_type="image",
            user_id=self.running.current_user_id,
            chat_id=None,
            turn_id=None,
            latency_class="standard",
            model_key=None,
            ordering_key="test:background-binding-blocker",
            execution=JobExecution(execute=block_media_lane),
        )
        self.assertTrue(blocker_started.wait(1))
        background_release = threading.Event()
        queue = self.running.services.jobs.queue
        original_admission_check = queue.admission_check

        def hold_background_models(job):
            if job.job_type in {"task_model", "memory_extraction"} and not background_release.is_set():
                return False
            return original_admission_check(job)

        queue.admission_check = hold_background_models

        accepted = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "Show me a garden.", "memory_mode": "saved"},
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        primary_job_id = accepted.json()["job"]["id"]
        deadline = time.monotonic() + 3
        primary = None
        while time.monotonic() < deadline:
            primary = self.client.get(f"/api/v1/jobs/{primary_job_id}").json()
            if primary["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(primary["status"], "completed")
        result = primary["result"]
        self.assertIn("title_job_id", result)
        self.assertIn("capability_planning_job_id", result)
        self.assertIn("memory_extraction_job_id", result)

        removed = self.client.put(
            f"/api/v1/personas/{self.first_persona['id']}",
            json={
                "workspace_id": self.second_workspace["id"],
                "workspace_ids": [self.second_workspace["id"]],
                "name": self.first_persona["name"],
                "system_prompt": self.first_persona["system_prompt"],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        background_release.set()
        queue.wake()
        blocker_release.set()
        self.assertEqual(self.running.wait_job(blocker.id)["status"], "completed")

        title = self.running.wait_job(result["title_job_id"])
        capability = self.running.wait_job(result["capability_planning_job_id"])
        extraction = self.running.wait_job(result["memory_extraction_job_id"])
        self.assertFalse(title["result"]["source_binding_valid"])
        self.assertFalse(capability["result"]["source_binding_valid"])
        self.assertFalse(extraction["result"]["source_binding_valid"])
        task_roles = [self.provider._task_role(request) for request in self.provider.task_requests]
        self.assertNotIn(TITLE_GENERATION, task_roles)
        self.assertNotIn(CAPABILITY_PLANNING, task_roles)
        self.assertNotIn(MEMORY_EXTRACTION, task_roles)
        self.assertEqual(self.client.get("/api/v1/memories").json()["items"], [])
        queue.admission_check = original_admission_check

    def test_title_followup_rechecks_binding_before_persisting(self):
        gate = threading.Event()
        self.provider.task_gates[TITLE_GENERATION] = gate
        self.provider.task_started[TITLE_GENERATION] = threading.Event()
        self.provider.task_outputs[TITLE_GENERATION] = {"title": "SHOULD NOT APPLY"}
        chat = self._chat()
        renamed = self.client.put(f"/api/v1/chats/{chat['id']}", json={"title": "New chat"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        accepted = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "A first request with a generated title.", "memory_mode": "off"},
        )
        self.assertEqual(accepted.status_code, 202, accepted.text)
        primary_job_id = accepted.json()["job"]["id"]
        deadline = time.monotonic() + 3
        primary = None
        while time.monotonic() < deadline:
            primary = self.client.get(f"/api/v1/jobs/{primary_job_id}").json()
            if primary["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(primary["status"], "completed")
        title_job_id = primary["result"]["title_job_id"]
        self.assertTrue(self.provider.task_started[TITLE_GENERATION].wait(1))
        deterministic_title = self.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"]

        removed = self.client.put(
            f"/api/v1/personas/{self.first_persona['id']}",
            json={
                "workspace_id": self.second_workspace["id"],
                "workspace_ids": [self.second_workspace["id"]],
                "name": self.first_persona["name"],
                "system_prompt": self.first_persona["system_prompt"],
            },
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        gate.set()

        title = self.running.wait_job(title_job_id)
        self.assertFalse(title["result"]["source_binding_valid"])
        current_title = self.client.get(f"/api/v1/chats/{chat['id']}").json()["chat"]["title"]
        self.assertEqual(current_title, deterministic_title)
        self.assertNotEqual(current_title, "SHOULD NOT APPLY")


if __name__ == "__main__":
    unittest.main()
