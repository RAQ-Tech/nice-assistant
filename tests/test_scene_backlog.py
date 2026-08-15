"""The per-persona scene backlog.

Pictures that have been proposed but not made. Kept separate from the retained
library because "we could make this" and "we have this" are different facts, and
conflating them would make a plan look like an achievement. Nothing generates
from it yet. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from tests.support import TestApp


SCENE = {
    "subject": "avery",
    "action": "reading on a sofa",
    "setting": "a lamplit room",
    "wardrobe": "an oversized jumper",
    "framing": "",
    "lighting": "",
    "camera": "",
    "mood": "quiet",
}


class SceneBacklogTests(unittest.TestCase):
    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}).json()

    def _propose(self, running, persona_id: str, scene=None, detail: str = "operator idea"):
        return running.client.post(
            "/api/v1/scene-backlog",
            # An empty scene is a real case to test, so it must not fall back.
            json={"persona_id": persona_id, "scene": SCENE if scene is None else scene, "source_detail": detail},
        )

    def test_a_proposal_records_what_it_is_and_where_it_came_from(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            created = self._propose(running, persona["id"])

            self.assertEqual(created.status_code, 201, created.text)
            entry = created.json()
            self.assertEqual(entry["state"], "proposed")
            self.assertEqual(entry["source"], "operator")
            self.assertEqual(entry["source_detail"], "operator idea")
            self.assertEqual(entry["summary"], "avery, reading on a sofa in a lamplit room")
            # Nothing has been made from it.
            self.assertIsNone(entry["media_id"])

    def test_a_proposal_needs_a_description(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            refused = self._propose(running, persona["id"], scene={})
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("describe the picture", refused.text)

    def test_a_proposal_must_name_a_persona_the_owner_has(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self.assertEqual(self._propose(running, "not-a-persona").status_code, 404)

    def test_an_operator_can_approve_and_retire_but_not_fake_progress(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            entry = self._propose(running, persona["id"]).json()

            approved = running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"})
            self.assertEqual(approved.status_code, 200, approved.text)
            self.assertEqual(approved.json()["state"], "approved")

            retired = running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "retired"})
            self.assertEqual(retired.json()["state"], "retired")

            # 'generating' and 'done' describe work, so they are not offered as
            # something to click.
            self.assertEqual(
                running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "done"}).status_code,
                422,
            )

    def test_a_retired_scene_can_be_reconsidered_but_not_approved_directly(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            entry = self._propose(running, persona["id"]).json()
            running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "retired"})

            refused = running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"})
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertIn("cannot be moved", refused.text)

            revived = running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "proposed"})
            self.assertEqual(revived.json()["state"], "proposed")

    def test_entries_filter_by_persona_and_state(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            first = self._propose(running, persona["id"]).json()
            self._propose(running, persona["id"], scene={**SCENE, "action": "walking a dog"})
            running.client.put(f"/api/v1/scene-backlog/{first['id']}/state", json={"state": "approved"})

            approved = running.client.get(
                "/api/v1/scene-backlog", params={"persona_id": persona["id"], "state": "approved"}
            ).json()["items"]
            self.assertEqual([item["id"] for item in approved], [first["id"]])

            everything = running.client.get("/api/v1/scene-backlog", params={"persona_id": persona["id"]}).json()[
                "items"
            ]
            self.assertEqual(len(everything), 2)

    def test_backlog_entries_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            entry = self._propose(running, persona["id"]).json()

            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            self.assertEqual(running.client.get("/api/v1/scene-backlog").json()["items"], [])
            self.assertEqual(running.client.delete(f"/api/v1/scene-backlog/{entry['id']}").status_code, 404)
            self.assertEqual(
                running.client.put(
                    f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"}
                ).status_code,
                404,
            )

    def test_a_proposal_can_be_deleted_outright(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            entry = self._propose(running, persona["id"]).json()

            self.assertEqual(running.client.delete(f"/api/v1/scene-backlog/{entry['id']}").status_code, 204)
            self.assertEqual(running.client.get("/api/v1/scene-backlog").json()["items"], [])


if __name__ == "__main__":
    unittest.main()


class ProductionReadinessTests(unittest.TestCase):
    def _persona(self, running) -> dict:
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        return running.client.post("/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}).json()

    def test_readiness_explains_why_nothing_is_being_made(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            readiness = running.client.get("/api/v1/scene-backlog/production-readiness")

            self.assertEqual(readiness.status_code, 200, readiness.text)
            body = readiness.json()
            # Off by default, and the reason says so rather than leaving an
            # operator to wonder whether it is broken.
            self.assertFalse(body["allowed"])
            self.assertFalse(body["enabled"])
            self.assertIn("switched off", body["reason"])
            self.assertEqual(body["approved_waiting"], 0)

    def test_readiness_counts_only_approved_scenes(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            persona = self._persona(running)
            entry = running.client.post(
                "/api/v1/scene-backlog",
                json={"persona_id": persona["id"], "scene": SCENE},
            ).json()

            self.assertEqual(
                running.client.get("/api/v1/scene-backlog/production-readiness").json()["approved_waiting"],
                0,
            )
            running.client.put(f"/api/v1/scene-backlog/{entry['id']}/state", json={"state": "approved"})
            self.assertEqual(
                running.client.get("/api/v1/scene-backlog/production-readiness").json()["approved_waiting"],
                1,
            )
