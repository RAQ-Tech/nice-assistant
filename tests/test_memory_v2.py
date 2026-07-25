from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.memory_service import (
    memory_candidate_is_sensitive,
    memory_search_query,
    normalize_memory_content,
)
from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


class InvalidMemoryProvider(FakeChatProvider):
    def generate(self, request, cancellation):
        if self._task_role(request) == "memory_extraction":
            self.task_requests.append(request)
            self.memory_requests.append(request)
            return "not valid candidate JSON"
        return super().generate(request, cancellation)


class MemoryV2Tests(unittest.TestCase):
    def test_post_turn_candidates_are_pending_source_persona_only_and_nonblocking(self):
        gate = threading.Event()
        provider = FakeChatProvider(
            ["Conversation complete."],
            memory_candidates=[
                {"content": "The user's favorite color is blue.", "confidence": 0.91},
                {"content": "The user owns a dog.", "confidence": 0.86},
            ],
            memory_gate=gate,
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            owner_id = running.create_and_login()
            _workspace, persona = running.ensure_bound_persona()
            chat = running.create_chat(
                {"title": "Candidate review", "memory_mode": "saved"},
                persona_id=persona["id"],
            )
            started_response = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "My favorite color is blue.", "memory_mode": "saved"},
            )
            self.assertEqual(started_response.status_code, 202, started_response.text)
            started = started_response.json()
            completed = running.wait_job(started["job"]["id"])
            self.assertEqual(completed["status"], "completed")
            extraction_job_id = completed["result"]["memory_extraction_job_id"]
            self.assertTrue(provider.memory_started.wait(1))
            self.assertIn(
                running.client.get(f"/api/v1/jobs/{extraction_job_id}").json()["status"],
                {"queued", "running"},
            )
            gate.set()
            extraction = running.wait_job(extraction_job_id)
            self.assertEqual(extraction["status"], "completed")
            self.assertEqual(extraction["result"]["candidate_count"], 2)
            self.assertTrue(extraction["result"]["source_binding_valid"])

            pending = running.client.get("/api/v1/memories?status=pending").json()["items"]
            candidate = next(item for item in pending if "favorite color" in item["content"])
            dog_candidate = next(item for item in pending if "owns a dog" in item["content"])
            self.assertEqual(candidate["confidence"], 0.91)
            self.assertEqual(candidate["source_type"], "conversation")
            self.assertEqual(candidate["source_turn_id"], started["turn"]["id"])
            self.assertEqual(candidate["source_message_id"], started["turn"]["user_message_id"])
            self.assertEqual(candidate["access_state"], "grants")
            self.assertEqual(candidate["origin"]["source_chat_id"], chat["id"])
            self.assertEqual(candidate["origin"]["source_persona_id"], persona["id"])
            self.assertEqual(candidate["origin"]["provenance_status"], "resolved")
            self.assertEqual(
                [(grant["grant_type"], grant["target_id"], grant["grant_source"]) for grant in candidate["grants"]],
                [("persona", persona["id"], "automatic_source_persona")],
            )

            before_approval = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What color do I prefer?", "memory_mode": "saved"},
            ).json()
            before_completed = running.wait_job(before_approval["job"]["id"])
            before_system = "\n".join(
                message["content"] for message in provider.requests[-1].messages if message["role"] == "system"
            ).casefold()
            self.assertNotIn("[saved memory context", before_system)
            running.wait_job(before_completed["result"]["memory_extraction_job_id"])

            approved = running.client.post(f"/api/v1/memories/{candidate['id']}/approve")
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["status"], "active")
            rejected = running.client.post(f"/api/v1/memories/{dog_candidate['id']}/reject")
            self.assertEqual(rejected.json()["status"], "rejected")

            after_approval = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What color do I prefer?", "memory_mode": "saved"},
            ).json()
            after_completed = running.wait_job(after_approval["job"]["id"])
            after_system = "\n".join(
                message["content"] for message in provider.requests[-1].messages if message["role"] == "system"
            ).casefold()
            self.assertIn("[saved memory context", after_system)
            self.assertIn("favorite color is blue", after_system)
            running.wait_job(after_completed["result"]["memory_extraction_job_id"])

            second_id = running.create_and_login("second")
            self.assertNotEqual(owner_id, second_id)
            self.assertEqual(running.client.get("/api/v1/memories").json()["items"], [])
            self.assertEqual(
                running.client.post(f"/api/v1/memories/{candidate['id']}/approve").status_code,
                404,
            )

    def test_extraction_revalidates_binding_and_never_falls_back_or_broadens(self):
        gate = threading.Event()
        provider = FakeChatProvider(
            ["Completed before extraction."],
            memory_candidates=[{"content": "The user prefers architectural diagrams.", "confidence": 0.93}],
            memory_gate=gate,
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            first = running.client.post("/api/v1/workspaces", json={"name": "First"}).json()
            second = running.client.post("/api/v1/workspaces", json={"name": "Second"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={
                    "workspace_id": first["id"],
                    "workspace_ids": [first["id"], second["id"]],
                    "name": "Avery",
                },
            ).json()
            chat = running.create_chat(
                {"title": "Binding changes", "memory_mode": "saved"},
                persona_id=persona["id"],
                context={"kind": "workspace", "workspace_id": first["id"]},
            )
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "I prefer architectural diagrams.", "memory_mode": "saved"},
            ).json()
            completed = running.wait_job(started["job"]["id"])
            extraction_job_id = completed["result"]["memory_extraction_job_id"]
            self.assertTrue(provider.memory_started.wait(1))

            changed = running.client.put(
                f"/api/v1/personas/{persona['id']}",
                json={
                    "workspace_id": second["id"],
                    "workspace_ids": [second["id"]],
                    "name": "Avery",
                },
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            gate.set()
            extraction = running.wait_job(extraction_job_id)
            self.assertEqual(extraction["status"], "completed")
            self.assertEqual(extraction["result"]["candidate_count"], 0)
            self.assertFalse(extraction["result"]["source_binding_valid"])
            self.assertEqual(running.client.get("/api/v1/memories").json()["items"], [])

    def test_sensitive_extraction_candidates_are_discarded_before_persistence(self):
        provider = FakeChatProvider(
            ["I will not retain that credential."],
            memory_candidates=[
                {
                    "content": "The user's temporary API key is sk-not-a-real-evaluation-secret.",
                    "confidence": 0.99,
                },
                {
                    "content": "The user prefers concise technical answers.",
                    "confidence": 0.91,
                },
            ],
        )
        self.assertTrue(memory_candidate_is_sensitive("The user's password is hunter2."))
        self.assertFalse(memory_candidate_is_sensitive("The user prefers concise technical answers."))
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.create_chat({"title": "Sensitive extraction", "memory_mode": "saved"})
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Do not save my temporary API key.", "memory_mode": "saved"},
            ).json()
            completed = running.wait_job(started["job"]["id"])
            extraction = running.wait_job(completed["result"]["memory_extraction_job_id"])
            self.assertEqual(extraction["status"], "completed")
            self.assertEqual(extraction["result"]["candidate_count"], 1)
            self.assertEqual(extraction["result"]["filtered_sensitive_count"], 1)
            memories = running.client.get("/api/v1/memories").json()["items"]
            self.assertEqual([item["content"] for item in memories], ["The user prefers concise technical answers."])

    def test_manual_revision_forget_history_and_undo_remain_durable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            _workspace, persona = running.ensure_bound_persona()
            created = running.client.post(
                "/api/v1/memories",
                json={
                    "content": "Prefers concise technical answers.",
                    "grants": [{"grant_type": "persona", "target_id": persona["id"]}],
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            original = created.json()

            revised = running.client.put(
                f"/api/v1/memories/{original['id']}",
                json={"content": "Prefers concise, evidence-backed technical answers."},
            )
            self.assertEqual(revised.status_code, 200, revised.text)
            revision = revised.json()
            self.assertNotEqual(revision["id"], original["id"])
            self.assertEqual(revision["supersedes_id"], original["id"])
            self.assertEqual(revision["origin"]["revision_of_memory_id"], original["id"])
            self.assertEqual(
                [(grant["grant_type"], grant["target_id"]) for grant in revision["grants"]],
                [("persona", persona["id"])],
            )

            undone = running.client.post(f"/api/v1/memories/{revision['id']}/undo")
            self.assertEqual(undone.status_code, 200, undone.text)
            self.assertEqual(undone.json()["id"], original["id"])
            self.assertEqual(undone.json()["status"], "active")

            forgotten = running.client.post(f"/api/v1/memories/{original['id']}/forget")
            self.assertEqual(forgotten.status_code, 200, forgotten.text)
            restored = running.client.post(f"/api/v1/memories/{original['id']}/undo")
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["status"], "active")

            history = running.client.get(f"/api/v1/memories/{original['id']}/history").json()
            actions = [event["action"] for event in history["events"]]
            self.assertIn("forgotten", actions)
            self.assertIn("undo_forgotten", actions)

    def test_only_current_granted_approved_records_reach_context(self):
        provider = FakeChatProvider(["Reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            _workspace, persona = running.ensure_bound_persona()
            chat = running.create_chat(
                {"title": "Retrieval", "memory_mode": "off"},
                persona_id=persona["id"],
            )
            active = running.client.post(
                "/api/v1/memories",
                json={
                    "content": "The observatory code name is Aurora.",
                    "grants": [{"grant_type": "persona", "target_id": persona["id"]}],
                },
            ).json()
            stale = running.client.post(
                "/api/v1/memories",
                json={
                    "content": "The observatory code name is stale-forbidden.",
                    "grants": [{"grant_type": "persona", "target_id": persona["id"]}],
                },
            ).json()
            running.client.put(
                f"/api/v1/memories/{stale['id']}",
                json={"validity_status": "stale"},
            )
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                legacy = uow.repo.create_memory(
                    user_id=user_id,
                    scope="global",
                    scope_id=None,
                    content="The observatory code name is legacy-forbidden.",
                    normalized_content=normalize_memory_content("The observatory code name is legacy-forbidden."),
                    status="active",
                    source_type="legacy",
                )
                retrieved = uow.repo.relevant_memories(
                    user_id,
                    workspace_id=None,
                    persona_id=persona["id"],
                    chat_id=chat["id"],
                    search_query=memory_search_query("What is the observatory code name?"),
                    limit=10,
                )
            self.assertIn(active["id"], {row.id for row in retrieved})
            self.assertNotIn(stale["id"], {row.id for row in retrieved})
            self.assertNotIn(legacy.id, {row.id for row in retrieved})

            legacy_response = next(
                item for item in running.client.get("/api/v1/memories").json()["items"] if item["id"] == legacy.id
            )
            self.assertEqual(legacy_response["access_state"], "legacy_quarantined")
            self.assertEqual(legacy_response["grants"], [])

    def test_extraction_failure_never_changes_a_completed_turn(self):
        provider = InvalidMemoryProvider(["Durable assistant reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.create_chat({"title": "Safe extraction failure", "memory_mode": "saved"})
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "My stable preference is concise answers.", "memory_mode": "saved"},
            ).json()
            completed = running.wait_job(started["job"]["id"])
            self.assertEqual(completed["status"], "completed")
            extraction = running.wait_job(completed["result"]["memory_extraction_job_id"])
            self.assertEqual(extraction["status"], "failed")
            self.assertEqual(extraction["error"], "Memory candidate extraction returned an invalid response.")
            turn = running.client.get(f"/api/v1/turns/{started['turn']['id']}").json()
            self.assertEqual(turn["status"], "completed")
            detail = running.client.get(f"/api/v1/chats/{chat['id']}").json()
            self.assertEqual(detail["messages"][-1]["text"], "Durable assistant reply.")
            self.assertEqual(running.client.get("/api/v1/memories").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
