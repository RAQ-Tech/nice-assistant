from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.identity_contracts import IdentityVerificationResult
from app.provider_contracts import MediaArtifact, ProviderHealth, ProviderStatus
from app.repositories import UnitOfWork
from app.task_contracts import CAPABILITY_PLANNING
from tests.support import FakeChatProvider, TestApp


class FakeImageProvider:
    def __init__(self):
        self.requests = []

    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return MediaArtifact("image", b"catalog-image", ".png", "image/png")


class ValidImageProvider(FakeImageProvider):
    def generate(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return MediaArtifact("image", identity_image(), ".png", "image/png")


class SequenceIdentityProvider:
    name = "compreface"

    def __init__(self, scores):
        self.scores = list(scores)
        self.requests = []

    def health(self, base_url, api_key, timeout_seconds):
        return ProviderHealth(self.name, ProviderStatus.READY, "ready")

    def verify(self, request, cancellation):
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        score = self.scores.pop(0)
        return IdentityVerificationResult(score, 1, 1, "fake-v1", "safe-request")


def identity_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (256, 256), (150, 90, 70)).save(output, format="PNG")
    return output.getvalue()


def model_payload(
    name: str,
    external_id: str,
    *,
    priority: int = 50,
    operations=None,
    domains=None,
    content_tags=None,
    features=None,
    vram=0,
    backend="automatic1111",
):
    return {
        "resource_type": "model",
        "kind": "image",
        "name": name,
        "provider_key": "local-image",
        "backend": backend,
        "external_id": external_id,
        "enabled": True,
        "priority": priority,
        "operations": operations or ["generate"],
        "domains": domains or [],
        "content_tags": content_tags or ["general"],
        "features": features or ["text_to_image"],
        "estimated_vram_mb": vram,
        "estimated_load_seconds": 1,
        "default_settings": {"steps": 24, "cfg_scale": 6.5, "allow_nsfw": True},
        "notes": "",
        "compatible_model_ids": [],
    }


def addon_payload(resource_type, name, external_id, model_id, *, content_tags=None, operations=None):
    default_settings = (
        {"weight": 0.8, "trigger_words": ["special pose"]} if resource_type == "lora" else {"workflow_patch": {}}
    )
    return {
        "resource_type": resource_type,
        "kind": "image",
        "name": name,
        "provider_key": "local-image",
        "backend": "comfyui" if resource_type == "workflow" else "automatic1111",
        "external_id": external_id,
        "enabled": True,
        "priority": 70,
        "operations": operations or ["generate"],
        "domains": [],
        "content_tags": content_tags or [],
        "features": [],
        "estimated_vram_mb": 500,
        "estimated_load_seconds": 1,
        "default_settings": default_settings,
        "notes": "",
        "compatible_model_ids": [model_id],
    }


class MediaCatalogTests(unittest.TestCase):
    def _identity_persona(self, running):
        workspace = running.client.post("/api/v1/workspaces", json={"name": "Identity world"}).json()
        persona = running.client.post(
            "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}
        ).json()
        running.client.put(
            f"/api/v1/personas/{persona['id']}/visual-identity",
            json={
                "appearance_description": "short copper hair and green eyes",
                "acceptance_threshold": 0.78,
                "max_generation_attempts": 2,
                "failure_policy": "block_claim",
            },
        )
        running.client.post(f"/api/v1/personas/{persona['id']}/visual-identity/consent", json={"attested": True})
        uploaded = running.client.post(
            f"/api/v1/personas/{persona['id']}/visual-identity/references",
            files={"file": ("avery.png", identity_image(), "image/png")},
            data={"provenance": "user_upload", "attested": "true"},
        ).json()
        running.client.post(f"/api/v1/identity-references/{uploaded['id']}/approval")
        return workspace, persona

    def test_identity_generation_retries_with_a_measured_correction_workflow(self):
        verifier = SequenceIdentityProvider([0.42, 0.93])
        chat_provider = FakeChatProvider(
            ["I can prepare that portrait."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a convention portrait",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": ["identity_control"],
                            "persona_subject": True,
                        }
                    ]
                }
            },
        )
        image_provider = ValidImageProvider()
        with (
            tempfile.TemporaryDirectory() as tmp,
            TestApp(Path(tmp), chat_provider=chat_provider, identity_providers={"compreface": verifier}) as running,
        ):
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/identity-validation/settings",
                json={
                    "provider": "compreface",
                    "base_url": "http://verifier.lan:8000",
                    "api_key": "verifier-secret",
                    "timeout_seconds": 10,
                },
            )
            workspace, persona = self._identity_persona(running)
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Comfy portrait",
                    "portrait.safetensors",
                    backend="comfyui",
                    operations=["generate", "image_to_image"],
                ),
            ).json()
            base = addon_payload("workflow", "Identity generation", "identity-gen", model["id"])
            base["features"] = ["identity_control"]
            base["default_settings"] = {
                "workflow_patch": {"100": {"class_type": "LoadImage", "inputs": {"image": "identity.png"}}},
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
            }
            running.client.post("/api/v1/media-catalog/resources", json=base)
            correction = addon_payload(
                "workflow", "Identity correction", "identity-correct", model["id"], operations=["image_to_image"]
            )
            correction["features"] = ["identity_control"]
            correction["default_settings"] = {
                "workflow_patch": {
                    "100": {"class_type": "LoadImage", "inputs": {"image": "identity.png"}},
                    "101": {"class_type": "LoadImage", "inputs": {"image": "source.png"}},
                },
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
                "source_image_bindings": [{"node_id": "101", "input_name": "image"}],
            }
            correction = running.client.post("/api/v1/media-catalog/resources", json=correction)
            self.assertEqual(correction.status_code, 201, correction.text)
            chat = running.client.post(
                "/api/v1/chats",
                json={"workspace_id": workspace["id"], "persona_id": persona["id"], "memory_mode": "off"},
            ).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns", json={"text": "Show me your outfit", "memory_mode": "off"}
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval").json()
            completed = running.wait_job(approved["job_id"])
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(completed["result"]["identityWorkflow"]["attempts"], 2)
            self.assertEqual(completed["result"]["identityConditioning"]["claim_status"], "verified")
            self.assertEqual(len(image_provider.requests), 2)
            self.assertEqual(image_provider.requests[1].options["operation"], "image_to_image")
            self.assertTrue(image_provider.requests[1].options["local_settings"]["source_image_path"])
            attempts = running.client.get(f"/api/v1/media-plans/{pending['media_plan']['id']}/attempts").json()["items"]
            self.assertEqual([item["status"] for item in attempts], ["failed", "passed"])
            self.assertEqual(attempts[1]["source_media_id"], attempts[0]["media_id"])

            verifier.scores.extend([0.21, 0.35])
            second_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Try another portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(second_turn["job"]["id"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            rejected_request = next(item for item in requests if item["id"] != pending["id"])
            rejected = running.client.post(f"/api/v1/capability-requests/{rejected_request['id']}/approval").json()
            rejected_job = running.wait_job(rejected["job_id"])
            self.assertEqual(rejected_job["status"], "failed")
            self.assertIn("did not meet", rejected_job["error"])
            rejected_attempts = running.client.get(
                f"/api/v1/media-plans/{rejected_request['media_plan']['id']}/attempts"
            ).json()["items"]
            self.assertEqual([item["status"] for item in rejected_attempts], ["failed", "failed"])

    def test_explicit_image_edit_requires_owner_source_and_exact_workflow_bindings(self):
        image_provider = ValidImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put("/api/v1/settings", json={"preferences": {"image_provider": "local/automatic1111"}})
            generated = running.client.post(
                "/api/v1/media/image-jobs", json={"prompt": "source", "provider": "local/automatic1111"}
            ).json()
            source = running.wait_job(generated["job_id"])["result"]["mediaId"]
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Comfy editor",
                    "editor.safetensors",
                    backend="comfyui",
                    operations=["generate", "image_to_image", "inpaint"],
                ),
            ).json()
            workflow = addon_payload("workflow", "Image editor", "edit-v1", model["id"], operations=["image_to_image"])
            workflow["default_settings"] = {
                "workflow_patch": {"100": {"class_type": "LoadImage", "inputs": {"image": "source.png"}}},
                "source_image_bindings": [{"node_id": "100", "input_name": "image"}],
            }
            self.assertEqual(running.client.post("/api/v1/media-catalog/resources", json=workflow).status_code, 201)
            edit = running.client.post(
                "/api/v1/media/image-edit-jobs",
                json={"prompt": "change the jacket to blue", "operation": "image_to_image", "source_media_id": source},
            )
            self.assertEqual(edit.status_code, 202, edit.text)
            completed = running.wait_job(edit.json()["job_id"])
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(image_provider.requests[-1].options["operation"], "image_to_image")
            self.assertEqual(
                running.client.post(
                    "/api/v1/media/image-edit-jobs",
                    json={"prompt": "mask edit", "operation": "inpaint", "source_media_id": source},
                ).status_code,
                409,
            )

    def test_catalog_crud_requires_explicit_same_owner_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            self.assertEqual(running.client.get("/api/v1/media-catalog").json()["resources"], [])
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload("Portrait model", "portrait.safetensors"),
            )
            self.assertEqual(model.status_code, 201, model.text)
            model = model.json()
            missing_compatibility = addon_payload(
                "lora",
                "Portrait detail",
                "detail.safetensors",
                model["id"],
            )
            missing_compatibility["compatible_model_ids"] = []
            rejected = running.client.post("/api/v1/media-catalog/resources", json=missing_compatibility)
            self.assertEqual(rejected.status_code, 400)
            lora = running.client.post(
                "/api/v1/media-catalog/resources",
                json=addon_payload("lora", "Portrait detail", "detail.safetensors", model["id"]),
            )
            self.assertEqual(lora.status_code, 201, lora.text)
            self.assertEqual(lora.json()["compatible_model_ids"], [model["id"]])
            incompatible_update = model_payload("Portrait model", "portrait.safetensors", backend="comfyui")
            self.assertEqual(
                running.client.put(
                    f"/api/v1/media-catalog/resources/{model['id']}",
                    json=incompatible_update,
                ).status_code,
                409,
            )

            other = {"username": "other", "password": "pass1234"}
            running.client.post("/api/v1/users", json=other)
            running.client.post("/api/v1/session", json=other)
            self.assertEqual(
                running.client.get(f"/api/v1/media-catalog/resources/{model['id']}").status_code,
                404,
            )
            self.assertEqual(running.client.get("/api/v1/media-catalog").json()["resources"], [])

    def test_planner_uses_metadata_compatibility_and_vram_not_filenames(self):
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.get("/api/v1/media-catalog")
            generic = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Generic high priority",
                    "fantasy-special-pose-by-filename.safetensors",
                    priority=100,
                    vram=4000,
                ),
            ).json()
            fantasy = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Fantasy model",
                    "neutral-name.safetensors",
                    priority=10,
                    domains=["fantasy"],
                    content_tags=["general", "explicit"],
                    vram=6500,
                ),
            ).json()
            self.assertIn(
                "identity_control",
                running.services.capabilities.planning_vocabulary(user_id)["features"],
            )
            lora = running.client.post(
                "/api/v1/media-catalog/resources",
                json=addon_payload(
                    "lora",
                    "Specific pose",
                    "pose.safetensors",
                    fantasy["id"],
                    content_tags=["pose.special"],
                ),
            )
            self.assertEqual(lora.status_code, 201, lora.text)
            preview = running.client.post(
                "/api/v1/media-catalog/plan-previews",
                json={
                    "kind": "image",
                    "operation": "generate",
                    "domains": ["fantasy"],
                    "content_tags": ["explicit", "pose.special"],
                    "required_features": ["text_to_image"],
                },
            )
            self.assertEqual(preview.status_code, 200, preview.text)
            plan = preview.json()
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(
                [item["id"] for item in plan["selected_resources"]],
                [fantasy["id"], lora.json()["id"]],
            )
            self.assertNotIn(generic["id"], [item["id"] for item in plan["selected_resources"]])
            self.assertEqual(plan["estimated_vram_mb"], 7000)

            budget = running.client.put(
                "/api/v1/media-catalog/settings",
                json={"vram_budget_mb": 6900, "max_loras": 4},
            )
            self.assertEqual(budget.status_code, 200, budget.text)
            blocked = running.client.post(
                "/api/v1/media-catalog/plan-previews",
                json={
                    "kind": "image",
                    "operation": "generate",
                    "domains": ["fantasy"],
                    "content_tags": ["explicit", "pose.special"],
                    "required_features": ["text_to_image"],
                },
            ).json()
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(blocked["block"]["code"], "no_compatible_media_plan")

    def test_comfy_workflows_require_executable_inline_content(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.get("/api/v1/media-catalog")
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload("Comfy model", "comfy.safetensors", backend="comfyui"),
            ).json()
            empty = addon_payload("workflow", "Empty workflow", "empty-workflow", model["id"])
            rejected = running.client.post("/api/v1/media-catalog/resources", json=empty)
            self.assertEqual(rejected.status_code, 400)
            self.assertIn("non-empty inline workflow patch", rejected.text)
            empty["enabled"] = False
            draft = running.client.post("/api/v1/media-catalog/resources", json=empty)
            self.assertEqual(draft.status_code, 201, draft.text)
            self.assertFalse(draft.json()["enabled"])

            workflow = addon_payload("workflow", "Identity stage", "identity-stage", model["id"])
            workflow["features"] = ["identity_control"]
            workflow["default_settings"] = {
                "workflow_patch": {
                    "100": {"class_type": "LoadImage", "inputs": {"image": "reviewed-reference.jpg"}},
                    "101": {"class_type": "OperatorIdentityStage", "inputs": {"image": ["100", 0]}},
                },
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
            }
            created = running.client.post("/api/v1/media-catalog/resources", json=workflow)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertIn(model["id"], created.json()["compatible_model_ids"])
            self.assertEqual(created.json()["features"], ["identity_control"])
            self.assertTrue(created.json()["default_settings"]["identity_image_bindings"])
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Identity readiness"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Ready persona"},
            ).json()
            identity_profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity")
            self.assertEqual(identity_profile.status_code, 200, identity_profile.text)
            with UnitOfWork(running.services.runtime.session_factory, running.services.runtime.secret_store) as uow:
                enabled_resources = uow.repo.media_catalog_resources(user_id, enabled=True)
                self.assertIn(model["id"], [item.id for item in enabled_resources])
                self.assertIn(created.json()["id"], [item.id for item in enabled_resources])
                self.assertEqual(
                    uow.repo.media_resource_compatible_model_ids(created.json()["id"]),
                    [model["id"]],
                )
            self.assertTrue(identity_profile.json()["generation_workflow_configured"])
            plan = running.client.post(
                "/api/v1/media-catalog/plan-previews",
                json={
                    "kind": "image",
                    "operation": "generate",
                    "domains": [],
                    "content_tags": [],
                    "required_features": ["identity_control"],
                },
            ).json()
            self.assertEqual(plan["status"], "blocked")
            self.assertEqual(plan["block"]["code"], "identity_persona_required")
            self.assertEqual(plan["identity_conditioning"]["status"], "blocked")
            self.assertEqual(
                [item["id"] for item in plan["selected_resources"]],
                [model["id"], created.json()["id"]],
            )

            missing_binding = addon_payload("workflow", "Unsafe identity stage", "unsafe-stage", model["id"])
            missing_binding["features"] = ["identity_control"]
            missing_binding["default_settings"] = {
                "workflow_patch": {"200": {"class_type": "LoadImage", "inputs": {"image": "placeholder"}}}
            }
            rejected_binding = running.client.post("/api/v1/media-catalog/resources", json=missing_binding)
            self.assertEqual(rejected_binding.status_code, 400)
            self.assertIn("explicit identity image binding", rejected_binding.text)

    def test_identity_aware_plan_uses_reviewed_reference_and_preserves_unverified_provenance(self):
        provider = FakeChatProvider(
            ["I can prepare that portrait."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a candid convention portrait",
                            "operation": "generate",
                            "domains": ["fantasy"],
                            "content_tags": [],
                            "required_features": ["identity_control"],
                            "persona_subject": True,
                        }
                    ]
                }
            },
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Identity world"}).json()
            persona = running.client.post(
                "/api/v1/personas",
                json={"workspace_id": workspace["id"], "name": "Avery"},
            ).json()
            profile = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "short copper hair, green eyes, a small cheek scar",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                },
            )
            self.assertEqual(profile.status_code, 200, profile.text)
            running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/consent",
                json={"attested": True},
            )
            uploaded = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references",
                files={"file": ("avery.png", identity_image(), "image/png")},
                data={"provenance": "user_upload", "attested": "true"},
            ).json()
            reference = running.client.post(f"/api/v1/identity-references/{uploaded['id']}/approval").json()

            running.client.get("/api/v1/media-catalog")
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Fantasy portrait model",
                    "fantasy.safetensors",
                    domains=["fantasy"],
                    backend="comfyui",
                    vram=7000,
                ),
            ).json()
            workflow = addon_payload("workflow", "Reviewed identity workflow", "identity-v1", model["id"])
            workflow["features"] = ["identity_control"]
            workflow["default_settings"] = {
                "workflow_patch": {
                    "100": {"class_type": "LoadImage", "inputs": {"image": "placeholder.jpg"}},
                    "101": {"class_type": "IdentityAdapter", "inputs": {"reference": ["100", 0]}},
                },
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
            }
            workflow = running.client.post("/api/v1/media-catalog/resources", json=workflow).json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "title": "Identity generation",
                    "memory_mode": "off",
                },
            ).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show me your convention outfit", "memory_mode": "off"},
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            conditioning = pending["media_plan"]["identity_conditioning"]
            self.assertEqual(pending["media_plan"]["status"], "ready")
            self.assertEqual(conditioning["persona_id"], persona["id"])
            self.assertEqual(conditioning["reference_id"], reference["id"])
            self.assertEqual(conditioning["workflow_resource_id"], workflow["id"])
            self.assertTrue(conditioning["appearance_description_included"])
            self.assertEqual(conditioning["verification_status"], "not_evaluated")
            self.assertNotIn("short copper hair", str(pending["media_plan"]))

            approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(approved.status_code, 200, approved.text)
            completed = running.wait_job(approved.json()["job_id"])
            self.assertEqual(completed["status"], "completed", completed)
            request = image_provider.requests[0]
            self.assertIn("short copper hair", request.prompt)
            self.assertEqual(request.options["backend"], "comfyui")
            local = request.options["local_settings"]
            self.assertEqual(local["identity_reference_sha256"], reference["sha256"])
            self.assertEqual(local["identity_image_bindings"], [{"node_id": "100", "input_name": "image"}])
            self.assertTrue(Path(local["identity_reference_path"]).is_file())

            capability = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            result_conditioning = capability["result"]["identityConditioning"]
            self.assertEqual(result_conditioning["status"], "conditioned")
            self.assertEqual(result_conditioning["claim_status"], "unverified")
            media_id = capability["result"]["mediaId"]
            status = running.client.get(f"/api/v1/media/{media_id}/identity-status").json()
            self.assertEqual(status["claim_status"], "unverified")
            self.assertEqual(status["conditioning"]["reference_id"], reference["id"])
            self.assertIsNone(status["validation"])
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                media = uow.repo.media(user_id, media_id)
                self.assertEqual(media.generation_plan_id, pending["media_plan"]["id"])

    def test_identity_plan_revalidates_profile_revision_before_approval(self):
        provider = FakeChatProvider(
            ["I can prepare that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "persona portrait",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": ["identity_control"],
                            "persona_subject": True,
                        }
                    ]
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Identity"}).json()
            persona = running.client.post(
                "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Avery"}
            ).json()
            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "green eyes",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                },
            )
            running.client.post(f"/api/v1/personas/{persona['id']}/visual-identity/consent", json={"attested": True})
            uploaded = running.client.post(
                f"/api/v1/personas/{persona['id']}/visual-identity/references",
                files={"file": ("avery.png", identity_image(), "image/png")},
                data={"provenance": "user_upload", "attested": "true"},
            ).json()
            running.client.post(f"/api/v1/identity-references/{uploaded['id']}/approval")
            running.client.get("/api/v1/media-catalog")
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload("Comfy", "comfy.safetensors", backend="comfyui"),
            ).json()
            workflow = addon_payload("workflow", "Identity", "identity", model["id"])
            workflow["features"] = ["identity_control"]
            workflow["default_settings"] = {
                "workflow_patch": {"100": {"class_type": "LoadImage", "inputs": {"image": "placeholder"}}},
                "identity_image_bindings": [{"node_id": "100", "input_name": "image"}],
            }
            running.client.post("/api/v1/media-catalog/resources", json=workflow)
            chat = running.client.post(
                "/api/v1/chats",
                json={"persona_id": persona["id"], "memory_mode": "off"},
            ).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns", json={"text": "A portrait", "memory_mode": "off"}
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "green eyes and silver hair",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                },
            )
            stale = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(stale.status_code, 409)
            self.assertIn("identity profile changed", stale.text)

    def test_coordinator_plan_is_visible_before_approval_and_drives_execution(self):
        provider = FakeChatProvider(
            ["I can prepare that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a fantasy portrait",
                            "operation": "generate",
                            "domains": ["fantasy"],
                            "content_tags": ["pose.special"],
                            "required_features": ["text_to_image"],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.get("/api/v1/media-catalog")
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Fantasy model",
                    "fantasy.safetensors",
                    domains=["fantasy"],
                    vram=6000,
                ),
            ).json()
            lora = running.client.post(
                "/api/v1/media-catalog/resources",
                json=addon_payload(
                    "lora",
                    "Specific pose",
                    "pose.safetensors",
                    model["id"],
                    content_tags=["pose.special"],
                ),
            ).json()
            chat = running.client.post("/api/v1/chats", json={"title": "Catalog", "memory_mode": "off"}).json()
            accepted = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Show a fantasy portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(accepted["job"]["id"])
            pending = running.client.get(
                "/api/v1/capability-requests",
                params={"chat_id": chat["id"]},
            ).json()["items"][0]
            self.assertEqual(pending["media_plan"]["status"], "ready")
            self.assertNotIn("a fantasy portrait", str(pending["media_plan"]).lower())
            self.assertEqual(
                [item["id"] for item in pending["media_plan"]["selected_resources"]],
                [model["id"], lora["id"]],
            )
            approved = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(approved.status_code, 200, approved.text)
            completed = running.wait_job(approved.json()["job_id"])
            self.assertEqual(completed["status"], "completed")
            options = image_provider.requests[0].options
            self.assertEqual(options["backend"], "automatic1111")
            self.assertEqual(options["local_settings"]["model"], "fantasy.safetensors")
            self.assertEqual(options["local_settings"]["loras"][0]["name"], "pose.safetensors")
            other = {"username": "plan-other", "password": "pass1234"}
            running.client.post("/api/v1/users", json=other)
            running.client.post("/api/v1/session", json=other)
            self.assertEqual(
                running.client.get(f"/api/v1/media-plans/{pending['media_plan']['id']}").status_code,
                404,
            )
            self.assertEqual(
                running.client.get(f"/api/v1/media-plans/{pending['media_plan']['id']}/attempts").status_code,
                404,
            )

    def test_blocked_and_stale_plans_cannot_be_approved(self):
        provider = FakeChatProvider(
            ["I can prepare that."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "edit the portrait",
                            "operation": "inpaint",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            running.client.get("/api/v1/media-catalog")
            model = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Editable model",
                    "editable.safetensors",
                    operations=["generate", "inpaint"],
                ),
            ).json()
            chat = running.client.post("/api/v1/chats", json={"title": "Blocked", "memory_mode": "off"}).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Inpaint this portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            self.assertEqual(pending, [])

            provider.task_outputs[CAPABILITY_PLANNING]["requests"][0]["operation"] = "generate"
            second = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Generate a portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(second["job"]["id"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            ready = next(item for item in requests if item["media_plan"]["status"] == "ready")
            updated = model_payload("Edited model", "editable.safetensors", operations=["generate", "inpaint"])
            self.assertEqual(
                running.client.put(f"/api/v1/media-catalog/resources/{model['id']}", json=updated).status_code,
                200,
            )
            stale = running.client.post(f"/api/v1/capability-requests/{ready['id']}/approval")
            self.assertEqual(stale.status_code, 409)
            self.assertIn("changed after", stale.text)

    def test_enabling_image_provider_after_catalog_initialization_restores_planning(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = FakeImageProvider()
            initial = running.client.get("/api/v1/media-catalog")
            self.assertEqual(initial.status_code, 200, initial.text)
            self.assertEqual(initial.json()["resources"], [])

            saved = running.client.put(
                "/api/v1/settings",
                json={
                    "preferences": {
                        "image_provider": "local/comfyui",
                        "image_local_model": "late-enable.safetensors",
                    }
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["preferences"]["image_provider"], "local")
            self.assertEqual(saved.json()["preferences"]["image_local_backend"], "comfyui")

            catalog = running.client.get("/api/v1/media-catalog").json()
            self.assertEqual(len(catalog["resources"]), 1)
            self.assertEqual(catalog["resources"][0]["backend"], "comfyui")
            self.assertEqual(catalog["resources"][0]["external_id"], "late-enable.safetensors")
            definitions = running.client.get("/api/v1/capabilities").json()["items"]
            image = next(item for item in definitions if item["key"] == "media.generate_image")
            self.assertTrue(image["available"])

    def test_enabling_image_provider_before_catalog_initialization_imports_once(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            saved = running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/comfyui"}},
            )
            self.assertEqual(saved.status_code, 200, saved.text)

            first = running.client.get("/api/v1/media-catalog")
            second = running.client.get("/api/v1/media-catalog")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(len(first.json()["resources"]), 1)
            self.assertEqual(first.json()["resources"], second.json()["resources"])

    def test_explicit_media_jobs_remain_a_truthfully_labeled_manual_fallback(self):
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/automatic1111"}},
            )
            started = running.client.post(
                "/api/v1/media/image-jobs",
                json={"prompt": "manual landscape", "provider": "local/automatic1111"},
            )
            self.assertEqual(started.status_code, 202, started.text)
            running.wait_job(started.json()["job_id"])
            capability = running.client.get(
                f"/api/v1/capability-requests/{started.json()['capability_request_id']}"
            ).json()
            self.assertEqual(capability["media_plan"]["source"], "manual")
            self.assertIn("bypasses catalog", capability["media_plan"]["explanation"]["summary"])


if __name__ == "__main__":
    unittest.main()
