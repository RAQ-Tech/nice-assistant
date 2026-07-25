from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from app.memory_service import memory_search_query, normalize_memory_content
from app.repositories import UnitOfWork
from tests.support import FakeChatProvider, TestApp


class MemoryGrantContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = FakeChatProvider(["Reply."])
        self.test_app = TestApp(Path(self.tmp.name), chat_provider=self.provider)
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.user_id = self.running.create_and_login()
        self.private = self._workspace("Private")
        self.studio = self._workspace("Studio")
        self.avery = self._persona("Avery", [self.private, self.studio])
        self.robin = self._persona("Robin", [self.private])

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _workspace(self, name):
        response = self.client.post("/api/v1/workspaces", json={"name": name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _persona(self, name, workspaces):
        response = self.client.post(
            "/api/v1/personas",
            json={
                "workspace_id": workspaces[0]["id"],
                "workspace_ids": [workspace["id"] for workspace in workspaces],
                "name": name,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _chat(self, persona, *, workspace=None, memory_mode="off"):
        context = {"kind": "workspace", "workspace_id": workspace["id"]} if workspace else {"kind": "personal"}
        response = self.client.post(
            "/api/v1/chats",
            json={
                "persona_id": persona["id"],
                "access_context": context,
                "memory_mode": memory_mode,
                "title": "Bound chat",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _create(self, content, grants, **metadata):
        response = self.client.post(
            "/api/v1/memories",
            json={"content": content, "grants": grants, **metadata},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _legacy_memory(self, content="Migrated pending fact."):
        with UnitOfWork(
            self.running.services.runtime.session_factory,
            self.running.services.runtime.secret_store,
        ) as uow:
            human = uow.repo.human_principal(self.user_id)
            row = uow.repo.create_memory(
                user_id=self.user_id,
                scope="global",
                scope_id=None,
                content=content,
                normalized_content=normalize_memory_content(content),
                status="pending",
                source_type="legacy",
            )
            uow.repo.create_memory_record(
                row.id,
                human_id=human.id,
                lineage="legacy_migrated",
                access_state="legacy_quarantined",
                memory_type="legacy_unknown",
                validity_status="legacy_unknown",
            )
            uow.repo.create_memory_origin(
                row.id,
                human_id=human.id,
                source_kind="legacy",
                provenance_status="legacy_unresolved",
            )
            return row.id

    def test_manual_memory_requires_explicit_access_and_rejects_credentials(self):
        missing = self.client.post(
            "/api/v1/memories",
            json={"content": "No access target was selected."},
        )
        self.assertEqual(missing.status_code, 422, missing.text)

        credential = self.client.post(
            "/api/v1/memories",
            json={
                "content": "The user's password is TEST_ONLY_DO_NOT_USE.",
                "grants": [{"grant_type": "persona", "target_id": self.avery["id"]}],
            },
        )
        self.assertEqual(credential.status_code, 400, credential.text)

        created = self._create(
            "The user prefers concise technical answers.",
            [
                {"grant_type": "persona", "target_id": self.avery["id"]},
                {"grant_type": "workspace", "target_id": self.studio["id"]},
            ],
        )
        self.assertEqual(created["access_state"], "grants")
        self.assertEqual(created["memory_type"], "durable")
        self.assertEqual(created["validity_status"], "current")
        self.assertIsNotNone(created["last_confirmed_at"])
        self.assertEqual(created["origin"]["source_kind"], "manual")
        self.assertEqual(created["origin"]["provenance_status"], "resolved")
        self.assertEqual(
            {(grant["grant_type"], grant["target_id"]) for grant in created["grants"]},
            {("persona", self.avery["id"]), ("workspace", self.studio["id"])},
        )
        # These fields are compatibility diagnostics only; all authority is in
        # the active grant list above.
        self.assertIn(created["scope"], {"persona", "workspace"})

    def test_revision_preserves_origin_and_grants_and_access_replacement_is_atomic(self):
        original = self._create(
            "Project Atlas is active.",
            [{"grant_type": "persona", "target_id": self.avery["id"]}],
            memory_type="stateful",
            stateful_status="active",
        )
        revised_response = self.client.put(
            f"/api/v1/memories/{original['id']}",
            json={
                "content": "Project Atlas is completed.",
                "stateful_status": "completed",
            },
        )
        self.assertEqual(revised_response.status_code, 200, revised_response.text)
        revised = revised_response.json()
        self.assertEqual(revised["origin"]["source_kind"], "edit")
        self.assertEqual(revised["origin"]["revision_of_memory_id"], original["id"])
        self.assertEqual(revised["stateful_status"], "completed")
        self.assertEqual(
            [(grant["grant_type"], grant["target_id"]) for grant in revised["grants"]],
            [("persona", self.avery["id"])],
        )

        replaced_response = self.client.put(
            f"/api/v1/memories/{revised['id']}/grants",
            json={"grants": [{"grant_type": "workspace", "target_id": self.private["id"]}]},
        )
        self.assertEqual(replaced_response.status_code, 200, replaced_response.text)
        replaced = replaced_response.json()
        self.assertEqual(
            [(grant["grant_type"], grant["target_id"]) for grant in replaced["grants"]],
            [("workspace", self.private["id"])],
        )

        invalid = self.client.put(
            f"/api/v1/memories/{revised['id']}/grants",
            json={"grants": [{"grant_type": "persona", "target_id": "missing-persona"}]},
        )
        self.assertEqual(invalid.status_code, 404, invalid.text)
        unchanged = self.client.get(f"/api/v1/memories/{revised['id']}/history").json()
        self.assertEqual(
            [(grant["grant_type"], grant["target_id"]) for grant in unchanged["memory"]["grants"]],
            [("workspace", self.private["id"])],
        )
        self.assertEqual(
            {event["action"] for event in unchanged["grant_events"]},
            {"granted", "revoked"},
        )

        revoked = self.client.put(
            f"/api/v1/memories/{revised['id']}/grants",
            json={"grants": []},
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["grants"], [])
        self.assertEqual(revoked.json()["content"], "Project Atlas is completed.")
        self.assertEqual(revoked.json()["origin"], revised["origin"])

        revised_while_revoked = self.client.put(
            f"/api/v1/memories/{revised['id']}",
            json={"content": "Project Atlas is completed and archived."},
        )
        self.assertEqual(revised_while_revoked.status_code, 200, revised_while_revoked.text)
        self.assertEqual(revised_while_revoked.json()["grants"], [])
        self.assertEqual(
            revised_while_revoked.json()["origin"]["revision_of_memory_id"],
            revised["id"],
        )

    def test_legacy_quarantined_memories_are_read_only_but_can_be_deleted(self):
        legacy_id = self._legacy_memory()
        current = next(item for item in self.client.get("/api/v1/memories").json()["items"] if item["id"] == legacy_id)
        self.assertEqual(current["access_state"], "legacy_quarantined")

        for action in ("approve", "reject", "forget", "undo"):
            with self.subTest(action=action):
                response = self.client.post(f"/api/v1/memories/{legacy_id}/{action}")
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn("read-only", response.json()["error"]["message"])

        edited = self.client.put(
            f"/api/v1/memories/{legacy_id}",
            json={"content": "Do not revise quarantined provenance."},
        )
        self.assertEqual(edited.status_code, 409, edited.text)
        self.assertIn("read-only", edited.json()["error"]["message"])

        grants = self.client.put(
            f"/api/v1/memories/{legacy_id}/grants",
            json={"grants": [{"grant_type": "persona", "target_id": self.avery["id"]}]},
        )
        self.assertEqual(grants.status_code, 409, grants.text)
        self.assertIn("read-only", grants.json()["error"]["message"])

        active = self._create(
            "A separately authorized memory.",
            [{"grant_type": "persona", "target_id": self.avery["id"]}],
        )
        bulk = self.client.post(
            "/api/v1/memories/bulk-actions",
            json={"action": "forget", "ids": [active["id"], legacy_id]},
        )
        self.assertEqual(bulk.status_code, 409, bulk.text)
        self.assertIn("read-only", bulk.json()["error"]["message"])
        unchanged = {item["id"]: item for item in self.client.get("/api/v1/memories").json()["items"]}
        self.assertEqual(unchanged[legacy_id]["status"], "pending")
        self.assertEqual(unchanged[active["id"]]["status"], "active")

        deleted = self.client.delete(f"/api/v1/memories/{legacy_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])

    def test_undo_revision_keeps_the_current_access_set(self):
        original = self._create(
            "The launch code name is Cobalt.",
            [{"grant_type": "persona", "target_id": self.avery["id"]}],
        )
        revised_response = self.client.put(
            f"/api/v1/memories/{original['id']}",
            json={"content": "The launch code name is Cobalt Blue."},
        )
        self.assertEqual(revised_response.status_code, 200, revised_response.text)
        revised = revised_response.json()
        replaced_response = self.client.put(
            f"/api/v1/memories/{revised['id']}/grants",
            json={"grants": [{"grant_type": "persona", "target_id": self.robin["id"]}]},
        )
        self.assertEqual(replaced_response.status_code, 200, replaced_response.text)

        undone_response = self.client.post(f"/api/v1/memories/{revised['id']}/undo")
        self.assertEqual(undone_response.status_code, 200, undone_response.text)
        undone = undone_response.json()
        self.assertEqual(undone["id"], original["id"])
        self.assertEqual(undone["status"], "active")
        self.assertEqual(
            [(grant["grant_type"], grant["target_id"]) for grant in undone["grants"]],
            [("persona", self.robin["id"])],
        )

        avery_chat = self._chat(self.avery)
        robin_chat = self._chat(self.robin)
        query = memory_search_query("Cobalt")
        with UnitOfWork(
            self.running.services.runtime.session_factory,
            self.running.services.runtime.secret_store,
        ) as uow:
            avery_ids = {
                row.id
                for row in uow.repo.relevant_memories(
                    self.user_id,
                    chat_id=avery_chat["id"],
                    search_query=query,
                )
            }
            robin_ids = {
                row.id
                for row in uow.repo.relevant_memories(
                    self.user_id,
                    chat_id=robin_chat["id"],
                    search_query=query,
                )
            }
        self.assertNotIn(original["id"], avery_ids)
        self.assertIn(original["id"], robin_ids)

    def test_fts_order_is_invariant_to_unauthorized_persona_and_owner_corpora(self):
        beta = self._create(
            "beta",
            [{"grant_type": "persona", "target_id": self.avery["id"]}],
        )
        alpha = self._create(
            "alpha",
            [{"grant_type": "persona", "target_id": self.avery["id"]}],
        )
        chat = self._chat(self.avery)
        query = memory_search_query("alpha beta")

        def relevant_ids():
            with UnitOfWork(
                self.running.services.runtime.session_factory,
                self.running.services.runtime.secret_store,
            ) as uow:
                return [
                    row.id
                    for row in uow.repo.relevant_memories(
                        self.user_id,
                        chat_id=chat["id"],
                        search_query=query,
                    )
                ]

        with UnitOfWork(
            self.running.services.runtime.session_factory,
            self.running.services.runtime.secret_store,
        ) as uow:
            uow.repo.memory(self.user_id, beta["id"]).updated_at = 100
            uow.repo.memory(self.user_id, alpha["id"]).updated_at = 200
        expected = [alpha["id"], beta["id"]]
        self.assertEqual(relevant_ids(), expected)

        for index in range(24):
            self._create(
                f"alpha private persona corpus {index}",
                [{"grant_type": "persona", "target_id": self.robin["id"]}],
            )
        self.assertEqual(relevant_ids(), expected)

        second_user_id = self.running.create_and_login("second")
        second_workspace = self._workspace("Second owner")
        second_persona = self._persona("Second persona", [second_workspace])
        for index in range(24):
            self._create(
                f"alpha other owner corpus {index}",
                [{"grant_type": "persona", "target_id": second_persona["id"]}],
            )
        self.assertNotEqual(second_user_id, self.user_id)
        login = self.client.post(
            "/api/v1/session",
            json={"username": "owner", "password": "pass1234"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.running.current_user_id = self.user_id
        self.assertEqual(relevant_ids(), expected)

    def test_proposal_derives_one_verified_source_persona_grant(self):
        chat = self._chat(self.avery, workspace=self.studio)
        started = self.client.post(
            f"/api/v1/chats/{chat['id']}/turns",
            json={"text": "I prefer diagrams for complex systems.", "memory_mode": "off"},
        )
        self.assertEqual(started.status_code, 202, started.text)
        accepted = started.json()
        self.running.wait_job(accepted["job"]["id"])

        proposed_response = self.client.post(
            "/api/v1/memory-proposals",
            json={
                "content": "The user prefers diagrams for complex systems.",
                "source_message_id": accepted["turn"]["user_message_id"],
            },
        )
        self.assertEqual(proposed_response.status_code, 200, proposed_response.text)
        proposed = proposed_response.json()
        self.assertEqual(proposed["status"], "pending")
        self.assertEqual(
            [(grant["grant_type"], grant["target_id"]) for grant in proposed["grants"]],
            [("persona", self.avery["id"])],
        )
        self.assertEqual(proposed["origin"]["source_chat_id"], chat["id"])
        self.assertEqual(proposed["origin"]["source_persona_id"], self.avery["id"])
        self.assertEqual(proposed["origin"]["source_workspace_id"], self.studio["id"])
        self.assertEqual(proposed["origin"]["provenance_status"], "resolved")

        missing_source = self.client.post(
            "/api/v1/memory-proposals",
            json={"content": "This has no attributable source."},
        )
        self.assertEqual(missing_source.status_code, 422, missing_source.text)

    def test_workspace_grants_follow_current_membership_including_future_personas(self):
        memory = self._create(
            "The Northwind proposal uses the blue plan.",
            [{"grant_type": "workspace", "target_id": self.private["id"]}],
        )
        self.assertEqual(memory["status"], "active")

        future = self._persona("Future", [self.private])
        workspace_chat = self._chat(future, workspace=self.private)
        started = self.client.post(
            f"/api/v1/chats/{workspace_chat['id']}/turns",
            json={"text": "Which Northwind plan is used?", "memory_mode": "saved"},
        ).json()
        completed = self.running.wait_job(started["job"]["id"])
        prompt = "\n".join(message["content"] for message in self.provider.requests[-1].messages)
        self.assertIn("Northwind proposal uses the blue plan", prompt)
        extraction_job_id = completed["result"].get("memory_extraction_job_id")
        if extraction_job_id:
            self.running.wait_job(extraction_job_id)

        personal_chat = self._chat(future)
        personal = self.client.post(
            f"/api/v1/chats/{personal_chat['id']}/turns",
            json={"text": "Which Northwind plan is used?", "memory_mode": "saved"},
        ).json()
        personal_completed = self.running.wait_job(personal["job"]["id"])
        personal_prompt = "\n".join(message["content"] for message in self.provider.requests[-1].messages)
        self.assertNotIn("Northwind proposal uses the blue plan", personal_prompt)
        extraction_job_id = personal_completed["result"].get("memory_extraction_job_id")
        if extraction_job_id:
            self.running.wait_job(extraction_job_id)

    def test_owner_profile_is_explicit_allowlisted_and_events_contain_names_only(self):
        blank = self.client.get("/api/v1/owner-profile")
        self.assertEqual(blank.status_code, 200, blank.text)
        self.assertIsNone(blank.json()["name"])
        self.assertEqual(blank.json()["revision"], 0)

        saved = self.client.put(
            "/api/v1/owner-profile",
            json={
                "name": "Chris",
                "pronouns": "they/them",
                "time_zone": "America/New_York",
                "measurement_units": "metric",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["name"], "Chris")
        self.assertEqual(saved.json()["revision"], 1)

        rejected = self.client.put(
            "/api/v1/owner-profile",
            json={"communication_needs": "My password is TEST_ONLY_DO_NOT_USE."},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)

        with UnitOfWork(
            self.running.services.runtime.session_factory,
            self.running.services.runtime.secret_store,
        ) as uow:
            payload = uow.session.execute(
                text("SELECT changed_fields_json FROM owner_profile_events ORDER BY created_at DESC, id DESC LIMIT 1")
            ).scalar_one()
        self.assertEqual(
            set(json.loads(payload)),
            {"name", "pronouns", "time_zone", "measurement_units"},
        )
        self.assertNotIn("Chris", payload)
        self.assertNotIn("America/New_York", payload)

    def test_owner_profile_is_available_to_each_persona_without_becoming_memory(self):
        saved = self.client.put(
            "/api/v1/owner-profile",
            json={
                "name": "Chris",
                "pronouns": "they/them",
                "preferred_language": "English",
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

        for persona in (self.avery, self.robin):
            with self.subTest(persona=persona["name"]):
                chat = self._chat(persona)
                started = self.client.post(
                    f"/api/v1/chats/{chat['id']}/turns",
                    json={"text": "Please introduce yourself.", "memory_mode": "off"},
                )
                self.assertEqual(started.status_code, 202, started.text)
                self.running.wait_job(started.json()["job"]["id"])
                prompt = "\n".join(message["content"] for message in self.provider.requests[-1].messages)
                self.assertIn("[Universal owner profile:", prompt)
                self.assertIn("- Name: Chris", prompt)
                self.assertIn("- Pronouns: they/them", prompt)
                self.assertIn("- Preferred language: English", prompt)

        self.assertEqual(self.client.get("/api/v1/memories").json()["items"], [])


if __name__ == "__main__":
    unittest.main()
