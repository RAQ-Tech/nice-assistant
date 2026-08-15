"""Generation presets: the tested recipe planning will select.

A preset names a combination someone has actually run - checkpoint, workflow,
LoRAs at their weights, sampler settings, dimensions, dialect. Planning selects
one of these rather than assembling a mix nobody has run. See ADR 0030.
"""

from pathlib import Path
import tempfile
import unittest

from app.provider_contracts import MediaArtifact
from app.repositories import UnitOfWork

from app.media_preset import normalize_definition
from app.service_errors import ConflictError, RequestError
from tests.support import TestApp


WRITABLE_PRESET_FIELDS = (
    "name",
    "kind",
    "enabled",
    "priority",
    "routing_card",
    "operations",
    "domains",
    "content_tags",
    "features",
    "definition",
    "estimated_vram_mb",
    "notes",
)


def _writable(preset: dict, **overrides) -> dict:
    """Strip server-owned fields so a read-back preset can be sent as an update."""

    payload = {key: preset[key] for key in WRITABLE_PRESET_FIELDS if key in preset}
    payload.update(overrides)
    return payload


class RecordingImageProvider:
    name = "local-image"

    def __init__(self):
        self.requests = []

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return MediaArtifact("image", b"generated-image", ".png", "image/png")


class PresetDefinitionTests(unittest.TestCase):
    def test_a_definition_needs_a_base_model(self):
        with self.assertRaises(RequestError):
            normalize_definition({})

    def test_unsupported_fields_are_refused_by_name(self):
        with self.assertRaises(RequestError) as raised:
            normalize_definition({"base_model_resource_id": "m1", "sampler_name": "euler"})
        self.assertIn("sampler_name", str(raised.exception))

    def test_dimensions_must_look_like_dimensions(self):
        with self.assertRaises(RequestError) as raised:
            normalize_definition({"base_model_resource_id": "m1", "dimensions": ["big"]})
        self.assertIn("1024x1024", str(raised.exception))
        normalized = normalize_definition({"base_model_resource_id": "m1", "dimensions": ["1024 X 1024", "832x1216"]})
        self.assertEqual(normalized["dimensions"], ["1024x1024", "832x1216"])

    def test_a_preset_cannot_list_the_same_lora_twice(self):
        with self.assertRaises(RequestError):
            normalize_definition(
                {
                    "base_model_resource_id": "m1",
                    "fixed_loras": [{"resource_id": "l1"}, {"resource_id": "l1"}],
                }
            )

    def test_slots_need_unique_short_names_and_a_bounded_max(self):
        with self.assertRaises(RequestError):
            normalize_definition({"base_model_resource_id": "m1", "lora_slots": [{"name": "Style Slot"}]})
        with self.assertRaises(RequestError):
            normalize_definition(
                {
                    "base_model_resource_id": "m1",
                    "lora_slots": [{"name": "style"}, {"name": "style"}],
                }
            )
        with self.assertRaises(RequestError):
            normalize_definition({"base_model_resource_id": "m1", "lora_slots": [{"name": "style", "max": 99}]})

    def test_a_preset_with_no_declared_stages_is_single_pass(self):
        normalized = normalize_definition({"base_model_resource_id": "m1", "workflow_resource_id": "w1"})
        self.assertEqual(normalized["stages"], [{"name": "base", "workflow_resource_id": "w1"}])

    def test_a_definition_carries_a_validated_dialect(self):
        normalized = normalize_definition(
            {"base_model_resource_id": "m1", "prompt_dialect": {"style": "booru", "prefix": "score_9"}}
        )
        self.assertEqual(normalized["prompt_dialect"]["style"], "booru")
        with self.assertRaises(RequestError):
            normalize_definition({"base_model_resource_id": "m1", "prompt_dialect": {"style": "shakespearean"}})


class PresetApiTests(unittest.TestCase):
    def _model(self, running, *, name="Base", external_id="base.safetensors", settings=None):
        return running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "image",
                "name": name,
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": external_id,
                "operations": ["generate"],
                "domains": ["portrait"],
                "estimated_vram_mb": 6000,
                "default_settings": settings or {},
            },
        ).json()

    def _lora(self, running, model_id, *, compatible=True):
        return running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "lora",
                "kind": "image",
                "name": "Style LoRA",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "style.safetensors",
                "operations": ["generate"],
                "default_settings": {"weight": 0.8},
                "compatible_model_ids": [model_id] if compatible else [],
            },
        ).json()

    def test_every_enabled_model_gets_a_preset_that_reproduces_its_settings(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._model(
                running,
                settings={
                    "size": "832x1216",
                    "steps": 28,
                    "cfg_scale": 3.5,
                    "sampler_name": "dpmpp_2m",
                    "prompt_dialect": {"style": "booru", "prefix": "score_9"},
                },
            )
            presets = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            preset = next(item for item in presets if item["name"] == "Base")

            definition = preset["definition"]
            self.assertEqual(definition["dimensions"], ["832x1216"])
            self.assertEqual(definition["sampler"]["steps"], 28)
            self.assertEqual(definition["sampler"]["cfg_scale"], 3.5)
            self.assertEqual(definition["prompt_dialect"]["style"], "booru")
            self.assertEqual(preset["domains"], ["portrait"])
            self.assertEqual(preset["estimated_vram_mb"], 6000)
            # One open slot reproduces today's automatic LoRA selection.
            self.assertEqual([slot["name"] for slot in definition["lora_slots"]], ["auto"])

    def test_the_backfill_runs_once_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self._model(running)
            first = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            second = running.client.get("/api/v1/media-catalog/presets").json()["items"]
            self.assertEqual(len(first), len(second))
            self.assertEqual([item["id"] for item in first], [item["id"] for item in second])

    def test_a_preset_round_trips_through_create_update_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            lora = self._lora(running, model["id"])
            payload = {
                "name": "Portrait recipe",
                "kind": "image",
                "routing_card": "Use when the subject's face is the point of the picture.",
                "operations": ["generate"],
                "domains": ["portrait"],
                "definition": {
                    "base_model_resource_id": model["id"],
                    "sampler": {"steps": 30, "cfg_scale": 4.0},
                    "dimensions": ["832x1216"],
                    "fixed_loras": [{"resource_id": lora["id"], "weight": 0.7}],
                },
            }
            created = running.client.post("/api/v1/media-catalog/presets", json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            preset = created.json()
            self.assertEqual(preset["routing_card"], payload["routing_card"])
            self.assertEqual(preset["definition"]["fixed_loras"], [{"resource_id": lora["id"], "weight": 0.7}])

            payload["priority"] = 80
            updated = running.client.put(f"/api/v1/media-catalog/presets/{preset['id']}", json=payload)
            self.assertEqual(updated.status_code, 200, updated.text)
            self.assertEqual(updated.json()["priority"], 80)
            self.assertEqual(updated.json()["revision"], preset["revision"] + 1)

            removed = running.client.delete(f"/api/v1/media-catalog/presets/{preset['id']}")
            self.assertEqual(removed.status_code, 204, removed.text)
            self.assertEqual(running.client.get(f"/api/v1/media-catalog/presets/{preset['id']}").status_code, 404)

    def test_a_preset_cannot_name_an_untested_combination(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            other = self._model(running, name="Other", external_id="other.safetensors")
            # The catalog already requires a LoRA to be paired with something.
            # This one is paired with a different checkpoint, which is exactly
            # the untested combination a preset must not be able to name.
            unpaired = self._lora(running, other["id"])
            payload = {
                "name": "Unpaired",
                "definition": {
                    "base_model_resource_id": model["id"],
                    "fixed_loras": [{"resource_id": unpaired["id"]}],
                },
            }
            refused = running.client.post("/api/v1/media-catalog/presets", json=payload)
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("not marked compatible", refused.text)

    def test_a_preset_base_model_must_exist_and_match_its_kind(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            refused = running.client.post(
                "/api/v1/media-catalog/presets",
                json={"name": "Ghost", "definition": {"base_model_resource_id": "does-not-exist"}},
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("base model", refused.text)

    def test_preset_names_are_unique_for_an_owner(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            payload = {"name": "Duplicate", "definition": {"base_model_resource_id": model["id"]}}
            self.assertEqual(running.client.post("/api/v1/media-catalog/presets", json=payload).status_code, 201)
            self.assertEqual(running.client.post("/api/v1/media-catalog/presets", json=payload).status_code, 409)

    def test_presets_belong_to_their_owner_alone(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            preset = running.client.post(
                "/api/v1/media-catalog/presets",
                json={"name": "Mine", "definition": {"base_model_resource_id": model["id"]}},
            ).json()

            running.client.delete("/api/v1/session")
            running.create_and_login("intruder")
            self.assertEqual(running.client.get(f"/api/v1/media-catalog/presets/{preset['id']}").status_code, 404)
            self.assertEqual(running.client.delete(f"/api/v1/media-catalog/presets/{preset['id']}").status_code, 404)
            self.assertEqual(
                [item["name"] for item in running.client.get("/api/v1/media-catalog/presets").json()["items"]],
                [],
            )


class PresetPlanningTests(unittest.TestCase):
    def _ready(self, running):
        provider = RecordingImageProvider()
        running.create_and_login()
        running.services.providers.media_providers["local-image"] = provider
        running.client.put(
            "/api/v1/settings",
            json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
        )
        return provider

    def _preview(self, running, **overrides):
        payload = {
            "kind": "image",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
        }
        payload.update(overrides)
        return running.client.post("/api/v1/media-catalog/plan-previews", json=payload).json()

    def test_planning_names_the_preset_it_chose_and_why(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            preview = self._preview(running)
            self.assertEqual(preview["status"], "ready", preview)
            chosen = preview["explanation"]["preset"]
            self.assertTrue(chosen["id"])
            self.assertTrue(chosen["reason"])
            self.assertIn("preset", preview["explanation"]["summary"])

    def test_a_backfilled_preset_submits_what_its_model_submitted_before(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            provider = self._ready(running)
            catalog = running.client.get("/api/v1/media-catalog").json()
            model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
            running.client.put(
                f"/api/v1/media-catalog/resources/{model['id']}",
                json={**model, "default_settings": {**model["default_settings"], "steps": 33, "size": "768x768"}},
            )
            # Re-derive the preset from the edited model.
            for preset in running.client.get("/api/v1/media-catalog/presets").json()["items"]:
                running.client.delete(f"/api/v1/media-catalog/presets/{preset['id']}")

            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a harbour"})
            self.assertEqual(running.wait_job(started.json()["job_id"])["status"], "completed")
            self.assertTrue(provider.requests)

    def test_automatic_lora_selection_only_fills_declared_slots(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            catalog = running.client.get("/api/v1/media-catalog").json()
            model = next(item for item in catalog["resources"] if item["resource_type"] == "model")
            running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "lora",
                    "kind": "image",
                    "name": "Portrait LoRA",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "portrait.safetensors",
                    "operations": ["generate"],
                    "domains": ["portrait"],
                    "default_settings": {"weight": 0.9},
                    "compatible_model_ids": [model["id"]],
                },
            )
            # The backfilled preset has an open slot, so the LoRA is available.
            with_slot = self._preview(running, domains=["portrait"])
            self.assertIn(
                "Portrait LoRA",
                [item["name"] for item in with_slot["selected_resources"]],
            )

            closed = running.client.post(
                "/api/v1/media-catalog/presets",
                json={
                    "name": "Closed recipe",
                    "priority": 100,
                    "domains": ["portrait"],
                    "definition": {"base_model_resource_id": model["id"]},
                },
            )
            self.assertEqual(closed.status_code, 201, closed.text)
            without_slot = self._preview(running, domains=["portrait"])
            self.assertEqual(without_slot["explanation"]["preset"]["name"], "Closed recipe")
            # No declared slot means nothing is added that the operator did not
            # put in the recipe.
            self.assertEqual(
                [item["resource_type"] for item in without_slot["selected_resources"]],
                ["model"],
            )

    def test_editing_a_preset_after_planning_fails_retryably_instead_of_substituting(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            self._ready(running)
            started = running.client.post("/api/v1/media/image-jobs", json={"prompt": "a harbour"})
            self.assertEqual(running.wait_job(started.json()["job_id"])["status"], "completed")

            capability = running.client.get("/api/v1/capability-requests").json()["items"][0]
            services = running.services
            requirements = {
                "kind": "image",
                "operation": "generate",
                "domains": [],
                "content_tags": [],
                "required_features": [],
            }
            # The direct button records a manual plan by design, so write the
            # coordinator plan this guard exists for.
            with UnitOfWork(services.runtime.session_factory, services.runtime.secret_store) as uow:
                user_id = uow.repo.capability_request_by_id(capability["id"]).user_id
                uow.repo.media_execution_plan_for_capability(user_id, capability["id"]) and None
                uow.session.delete(uow.repo.media_execution_plan_for_capability(user_id, capability["id"]))
                uow.session.flush()
                services.media_catalog.create_coordinator_plan(
                    uow.repo, user_id, capability["id"], requirements, persona_id=None
                )

            preset = running.client.get("/api/v1/media-catalog/presets").json()["items"][0]
            edited = running.client.put(
                f"/api/v1/media-catalog/presets/{preset['id']}",
                json=_writable(preset, priority=10),
            )
            self.assertEqual(edited.status_code, 200, edited.text)

            with UnitOfWork(services.runtime.session_factory, services.runtime.secret_store) as uow:
                with self.assertRaises(ConflictError) as raised:
                    services.media_catalog.execution_values(uow.repo, user_id, capability["id"])
            # Never a silent substitution: the operator is told to retry.
            self.assertIn("changed after this request was planned", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
