import tempfile
import threading
import unittest
from pathlib import Path

from app.memory_service import memory_candidate_is_sensitive, memory_search_query, normalize_memory_content
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
    def test_sensitive_extraction_candidates_are_discarded_before_persistence(self):
        provider = FakeChatProvider(
            ["I will not retain that credential."],
            memory_candidates=[
                {
                    "content": "The user's temporary API key is sk-not-a-real-evaluation-secret.",
                    "scope": "global",
                    "confidence": 0.99,
                },
                {
                    "content": "The user prefers concise technical answers.",
                    "scope": "global",
                    "confidence": 0.91,
                },
            ],
        )
        self.assertTrue(memory_candidate_is_sensitive("The user's password is hunter2."))
        self.assertFalse(memory_candidate_is_sensitive("The user prefers concise technical answers."))
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Sensitive extraction"}).json()
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

    def test_manual_revision_forget_history_and_undo_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            created = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "Prefers concise technical answers."},
            )
            self.assertEqual(created.status_code, 200, created.text)
            original = created.json()
            self.assertEqual(original["status"], "active")
            self.assertEqual(original["source_type"], "manual")

            revised = running.client.put(
                f"/api/v1/memories/{original['id']}",
                json={"content": "Prefers concise, evidence-backed technical answers."},
            )
            self.assertEqual(revised.status_code, 200, revised.text)
            revision = revised.json()
            self.assertNotEqual(revision["id"], original["id"])
            self.assertEqual(revision["supersedes_id"], original["id"])
            statuses = {item["id"]: item["status"] for item in running.client.get("/api/v1/memories").json()["items"]}
            self.assertEqual(statuses[original["id"]], "superseded")
            self.assertEqual(statuses[revision["id"]], "active")

            undone = running.client.post(f"/api/v1/memories/{revision['id']}/undo")
            self.assertEqual(undone.status_code, 200, undone.text)
            self.assertEqual(undone.json()["id"], original["id"])
            self.assertEqual(undone.json()["status"], "active")

            forgotten = running.client.delete(f"/api/v1/memories/{original['id']}")
            self.assertEqual(forgotten.status_code, 200, forgotten.text)
            self.assertEqual(forgotten.json()["memory"]["status"], "forgotten")
            restored = running.client.post(f"/api/v1/memories/{original['id']}/undo")
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["status"], "active")

            history = running.client.get(f"/api/v1/memories/{original['id']}/history")
            self.assertEqual(history.status_code, 200, history.text)
            actions = [event["action"] for event in history.json()["events"]]
            self.assertIn("forgotten", actions)
            self.assertIn("undo_forgotten", actions)
            self.assertTrue(
                any(event["undone_at"] for event in history.json()["events"] if event["action"] == "forgotten")
            )

    def test_post_turn_candidates_are_nonblocking_pending_and_owner_scoped(self):
        gate = threading.Event()
        provider = FakeChatProvider(
            ["Conversation complete."],
            memory_candidates=[
                {"content": "The user's favorite color is blue.", "scope": "global", "confidence": 0.91},
                {"content": "The user owns a dog.", "scope": "chat", "confidence": 0.86},
            ],
            memory_gate=gate,
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            owner_id = running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Candidate review"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "My favorite color is blue.", "memory_mode": "saved"},
            ).json()
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

            pending = running.client.get("/api/v1/memories?status=pending").json()["items"]
            candidate = next(item for item in pending if "favorite color" in item["content"])
            dog_candidate = next(item for item in pending if "owns a dog" in item["content"])
            self.assertEqual(candidate["confidence"], 0.91)
            self.assertEqual(candidate["source_type"], "conversation")
            self.assertEqual(candidate["source_turn_id"], started["turn"]["id"])
            self.assertEqual(candidate["source_message_id"], started["turn"]["user_message_id"])

            before_approval = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What color do I prefer?", "memory_mode": "saved"},
            ).json()
            before_job = running.wait_job(before_approval["job"]["id"])
            before_system = "\n".join(
                message["content"] for message in provider.requests[-1].messages if message["role"] == "system"
            ).casefold()
            self.assertNotIn("[saved memory context", before_system)
            running.wait_job(before_job["result"]["memory_extraction_job_id"])

            approved = running.client.post(f"/api/v1/memories/{candidate['id']}/approve")
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["status"], "active")
            rejected = running.client.post(f"/api/v1/memories/{dog_candidate['id']}/reject")
            self.assertEqual(rejected.json()["status"], "rejected")
            self.assertEqual(
                running.client.post(f"/api/v1/memories/{dog_candidate['id']}/undo").json()["status"],
                "pending",
            )

            after_approval = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What color do I prefer?", "memory_mode": "saved"},
            ).json()
            after_job = running.wait_job(after_approval["job"]["id"])
            after_system = "\n".join(
                message["content"] for message in provider.requests[-1].messages if message["role"] == "system"
            ).casefold()
            self.assertIn("[saved memory context", after_system)
            self.assertIn("favorite color is blue", after_system)
            running.wait_job(after_job["result"]["memory_extraction_job_id"])

            second_id = running.create_and_login("second")
            self.assertNotEqual(owner_id, second_id)
            self.assertEqual(running.client.get("/api/v1/memories").json()["items"], [])
            self.assertEqual(
                running.client.post(f"/api/v1/memories/{candidate['id']}/approve").status_code,
                404,
            )

    def test_only_approved_scoped_fts_results_reach_context(self):
        provider = FakeChatProvider(["Reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Retrieval"}).json()
            active = running.client.post(
                "/api/v1/memories",
                json={"scope": "chat", "scope_id": chat["id"], "content": "The observatory code name is Aurora."},
            ).json()
            unrelated = running.client.post(
                "/api/v1/memories",
                json={"scope": "global", "content": "The preferred lunch is ramen."},
            ).json()
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                pending = uow.repo.create_memory(
                    user_id=user_id,
                    scope="chat",
                    scope_id=chat["id"],
                    content="The observatory password is forbidden-pending.",
                    normalized_content=normalize_memory_content("The observatory password is forbidden-pending."),
                    status="pending",
                    source_type="conversation",
                )
                uow.repo.add_memory_event(
                    pending,
                    "candidate_created",
                    from_status=None,
                    to_status="pending",
                )
                retrieved = uow.repo.relevant_memories(
                    user_id,
                    workspace_id=None,
                    persona_id=None,
                    chat_id=chat["id"],
                    search_query=memory_search_query("What is the observatory code name?"),
                    limit=2,
                )
            self.assertEqual(retrieved[0].id, active["id"])
            self.assertNotIn(pending.id, {row.id for row in retrieved})
            self.assertIn(unrelated["id"], {row.id for row in retrieved})

            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "What is the observatory code name?"},
            ).json()
            running.wait_job(started["job"]["id"])
            system = provider.requests[-1].messages[0]["content"]
            self.assertIn("Aurora", system)
            self.assertNotIn("forbidden-pending", system)
            self.assertLess(system.index("Aurora"), system.index("ramen"))

    def test_extraction_failure_never_changes_a_completed_turn(self):
        provider = InvalidMemoryProvider(["Durable assistant reply."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={"title": "Safe extraction failure"}).json()
            started = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "My stable preference is concise answers."},
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

    def test_scope_deletion_archives_memory_instead_of_destroying_history(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Temporary"}).json()
            memory = running.client.post(
                "/api/v1/memories",
                json={
                    "scope": "workspace",
                    "scope_id": workspace["id"],
                    "content": "Workspace-specific preference.",
                },
            ).json()
            deleted = running.client.delete(f"/api/v1/workspaces/{workspace['id']}")
            self.assertEqual(deleted.status_code, 200, deleted.text)
            archived = next(
                item for item in running.client.get("/api/v1/memories").json()["items"] if item["id"] == memory["id"]
            )
            self.assertEqual(archived["status"], "forgotten")
            self.assertFalse(archived["can_undo"])
            history = running.client.get(f"/api/v1/memories/{memory['id']}/history").json()
            self.assertIn("scope_archived", [event["action"] for event in history["events"]])


if __name__ == "__main__":
    unittest.main()
