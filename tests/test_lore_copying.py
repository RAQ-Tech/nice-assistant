"""An entry can be taken from another persona, and then it is its own.

Two personas in the same setting want the same facts about that setting, and
retyping them is how the second one ends up subtly different from the first. So
an entry can be copied in one action.

What it must not do is follow the original. These pin that: a copy is a copy,
editing either leaves the other alone, and the workspace still bounds what can
be reached.
"""

from pathlib import Path
import tempfile
import unittest

from tests.support import TestApp


class _TwoPersonas(unittest.TestCase):
    """Two personas sharing a workspace, which is the situation this is for."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_app = TestApp(Path(self.tmp.name))
        self.running = self.test_app.__enter__()
        self.client = self.running.client
        self.running.create_and_login()
        self.workspace = self.client.post("/api/v1/workspaces", json={"name": "Home"}).json()
        self.ada = self._persona("Ada", self.workspace["id"])
        self.bo = self._persona("Bo", self.workspace["id"])

    def tearDown(self):
        self.test_app.__exit__(None, None, None)
        self.tmp.cleanup()

    def _persona(self, name: str, workspace_id: str) -> dict:
        created = self.client.post("/api/v1/personas", json={"workspace_id": workspace_id, "name": name})
        assert created.status_code == 200, created.text
        return created.json()

    def _entry(self, persona: dict, **overrides) -> dict:
        payload = {
            "title": "The lighthouse",
            "content": "The lighthouse on the point has been dark since 1974.",
            "keys": ["lighthouse"],
            "secondary_keys": [],
            "always_on": False,
            "case_sensitive": False,
            "priority": 70,
            "enabled": True,
        }
        payload.update(overrides)
        created = self.client.post(f"/api/v1/personas/{persona['id']}/lore", json=payload)
        assert created.status_code == 200, created.text
        return created.json()

    def _copy(self, target: dict, entry_id: str):
        return self.client.post(
            f"/api/v1/personas/{target['id']}/lore/copies",
            json={"source_entry_id": entry_id},
        )


class LoreCopyTests(_TwoPersonas):
    def test_a_copy_carries_everything_that_made_the_entry_work(self):
        source = self._entry(self.ada, keys=["lighthouse", "point"], priority=70, always_on=False)

        copied = self._copy(self.bo, source["id"])

        self.assertEqual(copied.status_code, 200, copied.text)
        body = copied.json()
        # Keys and priority are what make an entry fire at the right moment.
        # A copy that dropped them would look right in a list and behave wrong.
        self.assertEqual(body["title"], source["title"])
        self.assertEqual(body["content"], source["content"])
        self.assertEqual(body["keys"], source["keys"])
        self.assertEqual(body["priority"], source["priority"])
        self.assertNotEqual(body["id"], source["id"])

    def test_editing_either_copy_leaves_the_other_alone(self):
        source = self._entry(self.ada)
        copy_id = self._copy(self.bo, source["id"]).json()["id"]

        changed = self.client.put(
            f"/api/v1/personas/{self.bo['id']}/lore/{copy_id}",
            json={
                "title": "The lighthouse",
                "content": "The lighthouse was relit last spring.",
                "keys": ["lighthouse"],
                "secondary_keys": [],
                "always_on": False,
                "case_sensitive": False,
                "priority": 70,
                "enabled": True,
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)

        original = self.client.get(f"/api/v1/personas/{self.ada['id']}/lore").json()["items"][0]
        self.assertEqual(original["content"], "The lighthouse on the point has been dark since 1974.")

    def test_deleting_the_original_leaves_the_copy_standing(self):
        source = self._entry(self.ada)
        copy_id = self._copy(self.bo, source["id"]).json()["id"]

        self.client.delete(f"/api/v1/personas/{self.ada['id']}/lore/{source['id']}")

        surviving = self.client.get(f"/api/v1/personas/{self.bo['id']}/lore").json()["items"]
        self.assertEqual([row["id"] for row in surviving], [copy_id])

    def test_a_persona_in_another_workspace_cannot_be_reached(self):
        elsewhere = self.client.post("/api/v1/workspaces", json={"name": "Work"}).json()
        stranger = self._persona("Cy", elsewhere["id"])
        source = self._entry(self.ada)

        blocked = self._copy(stranger, source["id"])

        # A workspace is how somebody keeps unrelated work apart. Reaching
        # across one would make that separation advisory.
        self.assertEqual(blocked.status_code, 400, blocked.text)
        self.assertIn("same workspace", blocked.json()["error"]["message"])

    def test_copying_an_entry_onto_its_own_persona_is_refused(self):
        source = self._entry(self.ada)

        refused = self._copy(self.ada, source["id"])

        self.assertEqual(refused.status_code, 400, refused.text)

    def test_another_account_cannot_copy_out_of_this_one(self):
        source = self._entry(self.ada)
        self.client.delete("/api/v1/session")
        self.running.create_and_login("intruder")

        blocked = self._copy(self.bo, source["id"])

        self.assertEqual(blocked.status_code, 404, blocked.text)


class CopyableListingTests(_TwoPersonas):
    def _copyable(self, persona: dict) -> list[dict]:
        response = self.client.get(f"/api/v1/personas/{persona['id']}/lore/copyable")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["groups"]

    def test_a_sibling_persona_offers_what_it_has(self):
        self._entry(self.ada)

        groups = self._copyable(self.bo)

        self.assertEqual([group["persona_name"] for group in groups], ["Ada"])
        self.assertEqual([entry["title"] for entry in groups[0]["entries"]], ["The lighthouse"])

    def test_something_already_taken_is_not_offered_again(self):
        source = self._entry(self.ada)
        self._copy(self.bo, source["id"])

        # Offering a copy of something already copied is how a lore list fills
        # up with duplicates nobody meant to make.
        self.assertEqual(self._copyable(self.bo), [])

    def test_a_persona_in_another_workspace_is_not_listed(self):
        elsewhere = self.client.post("/api/v1/workspaces", json={"name": "Work"}).json()
        stranger = self._persona("Cy", elsewhere["id"])
        self._entry(stranger)

        self.assertEqual(self._copyable(self.bo), [])

    def test_a_persona_with_nothing_to_give_is_not_listed_as_empty(self):
        self.assertEqual(self._copyable(self.bo), [])


if __name__ == "__main__":
    unittest.main()
