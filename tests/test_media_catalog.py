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


def resource_write_payload(resource, **changes):
    keys = (
        "resource_type",
        "kind",
        "name",
        "provider_key",
        "backend",
        "external_id",
        "enabled",
        "priority",
        "operations",
        "domains",
        "content_tags",
        "features",
        "estimated_vram_mb",
        "estimated_load_seconds",
        "default_settings",
        "notes",
        "compatible_model_ids",
    )
    payload = {key: resource[key] for key in keys}
    payload.update(changes)
    return payload


class MediaCatalogTests(unittest.TestCase):
    def test_multiple_configured_backends_select_a_ready_fallback_deterministically(self):
        provider = FakeChatProvider(
            ["I’ll make that image."],
            task_outputs={
                CAPABILITY_PLANNING: {
                    "requests": [
                        {
                            "capability_key": "media.generate_image",
                            "prompt": "a lighthouse in a storm",
                            "operation": "generate",
                            "domains": [],
                            "content_tags": [],
                            "required_features": [],
                            "persona_subject": False,
                        }
                    ]
                }
            },
        )
        local = FakeImageProvider()
        openai = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            running.services.providers.media_providers["local-image"] = local
            running.services.providers.media_providers["openai-image"] = openai
            running.client.put("/api/v1/settings", json={"openai_api_key": "test-image-key", "preferences": {}})
            running.client.get("/api/v1/media-catalog")
            running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload("Preferred local", "local.safetensors", priority=90),
            )
            openai_model = model_payload("Ready OpenAI", "provider-default", priority=20)
            openai_model.update(
                {
                    "provider_key": "openai-image",
                    "backend": "openai",
                    "estimated_vram_mb": 0,
                    "default_settings": {"size": "1024x1024", "quality": "auto"},
                }
            )
            running.client.post("/api/v1/media-catalog/resources", json=openai_model)
            running.services.provider_service.check = lambda _user_id, check: {
                "ok": check == "openai",
                "provider": check,
                "status": "ready" if check == "openai" else "unreachable",
                "message": "ready" if check == "openai" else "unreachable",
            }
            chat = running.client.post("/api/v1/chats", json={"memory_mode": "off"}).json()
            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Create an image of a lighthouse in a storm", "memory_mode": "off"},
            ).json()
            chat_job = running.wait_job(turn["job"]["id"])
            running.wait_job(chat_job["result"]["followup_job_id"])
            request = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            if request["job_id"]:
                running.wait_job(request["job_id"])
            request = running.client.get(f"/api/v1/capability-requests/{request['id']}").json()
            model = next(
                item for item in request["media_plan"]["selected_resources"] if item["resource_type"] == "model"
            )
            self.assertEqual(model["provider_key"], "openai-image")
            self.assertEqual(len(openai.requests), 1)
            self.assertEqual(local.requests, [])

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
                "/api/v1/settings",
                json={"preferences": {}},
            )
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
            completed = running.wait_job(pending["job_id"])
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
            rejected_job = running.wait_job(rejected_request["job_id"])
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

            completed = running.wait_job(pending["job_id"])
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

    def test_missing_identity_workflow_uses_explicit_unconditioned_fallback_and_labels_results_truthfully(self):
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "a casual selfie of the selected persona",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": True,
        }
        chat_provider = FakeChatProvider(
            ["I can prepare that image."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/comfyui"}},
            )
            workspace, persona = self._identity_persona(running)
            profile = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertEqual(profile["conditioning_fallback"], "allow_unconditioned")
            running.client.get("/api/v1/media-catalog")
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "title": "Fallback identity generation",
                    "memory_mode": "off",
                },
            ).json()

            turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a casual selfie", "memory_mode": "off"},
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            plan = pending["media_plan"]
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["requirements"]["required_features"], ["identity_control"])
            self.assertEqual(plan["identity_conditioning"]["status"], "unconditioned")
            self.assertEqual(plan["identity_conditioning"]["claim_status"], "unverified")
            self.assertIn("No persona identity reference will be applied", " ".join(plan["explanation"]["warnings"]))

            self.assertEqual(pending["permission_mode"], "auto")
            completed = running.wait_job(pending["job_id"])
            self.assertEqual(completed["status"], "completed", completed)
            self.assertEqual(len(image_provider.requests), 1)
            request = image_provider.requests[0]
            self.assertIn("short copper hair", request.prompt)
            self.assertIsNone(request.options["local_settings"]["identity_reference_path"])
            capability = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            result = capability["result"]["identityConditioning"]
            self.assertEqual(result["status"], "unconditioned")
            self.assertEqual(result["claim_status"], "unverified")
            self.assertNotIn("identityWorkflow", capability["result"])
            self.assertIn("No persona identity reference was applied", capability["result"]["text"])
            self.assertIn("resemblance is not guaranteed", capability["result"]["text"])
            media_status = running.client.get(f"/api/v1/media/{capability['result']['mediaId']}/identity-status").json()
            self.assertEqual(media_status["claim_status"], "unverified")
            self.assertEqual(media_status["conditioning"]["status"], "unconditioned")

            required = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "short copper hair and green eyes",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                    "conditioning_fallback": "require_conditioning",
                },
            )
            self.assertEqual(required.status_code, 200, required.text)
            planned["prompt"] = "a strict selfie of the selected persona"
            second_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Try another selfie", "memory_mode": "off"},
            ).json()
            running.wait_job(second_turn["job"]["id"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            strict = next(item for item in requests if item["arguments"]["prompt"] == planned["prompt"])
            self.assertEqual(strict["status"], "failed")
            self.assertEqual(strict["permission_mode"], "auto")
            self.assertEqual(strict["media_plan"]["status"], "blocked")
            self.assertEqual(strict["media_plan"]["identity_conditioning"]["persona_id"], persona["id"])
            self.assertEqual(strict["attachment"]["status"], "failed")
            self.assertTrue(strict["attachment"]["retry_available"])
            self.assertIsNone(strict["job_id"])

            allowed = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "short copper hair and green eyes",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                    "conditioning_fallback": "allow_unconditioned",
                },
            )
            self.assertEqual(allowed.status_code, 200, allowed.text)
            retried = running.client.post(f"/api/v1/capability-requests/{strict['id']}/retry")
            self.assertEqual(retried.status_code, 200, retried.text)
            replacement = retried.json()
            self.assertEqual(replacement["permission_mode"], "auto")
            self.assertEqual(replacement["media_plan"]["status"], "ready")
            self.assertEqual(replacement["media_plan"]["identity_conditioning"]["status"], "unconditioned")
            self.assertIsNotNone(replacement["job_id"])
            self.assertEqual(running.wait_job(replacement["job_id"])["status"], "completed")

    def test_unconditioned_fallback_needs_no_saved_profile_consent_or_reference(self):
        planned = {
            "capability_key": "media.generate_image",
            "prompt": "a candid portrait of the selected persona",
            "operation": "generate",
            "domains": [],
            "content_tags": [],
            "required_features": [],
            "persona_subject": True,
        }
        chat_provider = FakeChatProvider(
            ["I can prepare that image.", "I can prepare another image.", "I cannot prepare that image yet."],
            task_outputs={CAPABILITY_PLANNING: {"requests": [planned]}},
        )
        image_provider = FakeImageProvider()
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=chat_provider) as running:
            user_id = running.create_and_login()
            running.services.providers.media_providers["local-image"] = image_provider
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local/comfyui"}},
            )
            workspace = running.client.post("/api/v1/workspaces", json={"name": "Fallback world"}).json()
            persona = running.client.post(
                "/api/v1/personas", json={"workspace_id": workspace["id"], "name": "Taylor"}
            ).json()
            implicit = running.client.get(f"/api/v1/personas/{persona['id']}/visual-identity").json()
            self.assertIsNone(implicit["id"])
            self.assertEqual(implicit["conditioning_fallback"], "allow_unconditioned")
            catalog = running.client.get("/api/v1/media-catalog").json()
            chat = running.client.post(
                "/api/v1/chats",
                json={
                    "workspace_id": workspace["id"],
                    "persona_id": persona["id"],
                    "title": "Default fallback",
                    "memory_mode": "off",
                },
            ).json()

            first_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send me a candid portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(first_turn["job"]["id"])
            first = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][0]
            self.assertEqual(first["media_plan"]["status"], "ready")
            self.assertEqual(first["media_plan"]["requirements"]["required_features"], ["identity_control"])
            self.assertEqual(first["media_plan"]["identity_conditioning"]["status"], "unconditioned")
            self.assertIsNone(first["media_plan"]["identity_conditioning"]["profile_id"])
            self.assertIsNone(first["media_plan"]["identity_conditioning"]["reference_id"])
            running.wait_job(first["job_id"])

            saved = running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "private draft appearance text",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                    "conditioning_fallback": "allow_unconditioned",
                },
            ).json()
            self.assertEqual(saved["status"], "draft")
            self.assertEqual(saved["consent_status"], "not_granted")
            self.assertEqual(saved["approved_reference_count"], 0)
            planned["prompt"] = "another candid portrait of the selected persona"
            second_turn = running.client.post(
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Send another candid portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(second_turn["job"]["id"])
            requests = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"]
            second = next(item for item in requests if item["arguments"]["prompt"] == planned["prompt"])
            self.assertEqual(second["media_plan"]["status"], "ready")
            self.assertEqual(second["media_plan"]["identity_conditioning"]["status"], "unconditioned")
            self.assertEqual(second["media_plan"]["identity_conditioning"]["profile_id"], saved["id"])
            self.assertFalse(second["media_plan"]["identity_conditioning"]["appearance_description_included"])
            running.wait_job(second["job_id"])

            self.assertEqual(len(image_provider.requests), 2)
            self.assertNotIn("private draft appearance text", image_provider.requests[1].prompt)
            self.assertIsNone(image_provider.requests[0].options["local_settings"]["identity_reference_path"])
            self.assertIsNone(image_provider.requests[1].options["local_settings"]["identity_reference_path"])

            running.client.put(
                "/api/v1/media-catalog/settings",
                json={"vram_budget_mb": 1, "max_loras": 4},
            )
            legacy = catalog["resources"][0]
            disabled = running.client.put(
                f"/api/v1/media-catalog/resources/{legacy['id']}",
                json=resource_write_payload(legacy, enabled=False),
            )
            self.assertEqual(disabled.status_code, 200, disabled.text)
            created = running.client.post(
                "/api/v1/media-catalog/resources",
                json=model_payload(
                    "Budget constrained model",
                    "budget.safetensors",
                    backend="comfyui",
                    vram=10,
                ),
            )
            self.assertEqual(created.status_code, 201, created.text)
            with UnitOfWork(
                running.services.runtime.session_factory,
                running.services.runtime.secret_store,
            ) as uow:
                blocked = running.services.media_catalog._build_plan(
                    uow.repo,
                    user_id,
                    {
                        "kind": "image",
                        "operation": "generate",
                        "domains": [],
                        "content_tags": [],
                        "required_features": ["identity_control"],
                    },
                    persona_id=persona["id"],
                )
            self.assertEqual(blocked["status"], "blocked")
            reasons = [reason for candidate in blocked["explanation"]["rejected"] for reason in candidate["reasons"]]
            self.assertTrue(any("vram" in reason.lower() for reason in reasons), reasons)
            self.assertFalse(any("identity_control" in reason for reason in reasons), reasons)

    def test_completed_identity_image_is_not_retroactively_changed_by_profile_update(self):
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
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_confirmation_policy": "always_ask"}},
            )
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
                f"/api/v1/chats/{chat['id']}/turns",
                json={"text": "Generate a portrait", "memory_mode": "off"},
            ).json()
            running.wait_job(turn["job"]["id"])
            pending = running.client.get("/api/v1/capability-requests", params={"chat_id": chat["id"]}).json()["items"][
                0
            ]
            self.assertEqual(pending["permission_mode"], "auto")
            self.assertEqual(running.wait_job(pending["job_id"])["status"], "completed")
            completed = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            media_id = completed["result"]["mediaId"]
            running.client.put(
                f"/api/v1/personas/{persona['id']}/visual-identity",
                json={
                    "appearance_description": "green eyes and silver hair",
                    "acceptance_threshold": 0.78,
                    "max_generation_attempts": 2,
                    "failure_policy": "block_claim",
                },
            )
            unchanged = running.client.get(f"/api/v1/capability-requests/{pending['id']}").json()
            self.assertEqual(unchanged["status"], "completed")
            self.assertEqual(unchanged["result"]["mediaId"], media_id)
            approval = running.client.post(f"/api/v1/capability-requests/{pending['id']}/approval")
            self.assertEqual(approval.status_code, 409, approval.text)

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
            completed = running.wait_job(pending["job_id"])
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

    def test_unsupported_operation_is_skipped_and_completed_plan_survives_catalog_edit(self):
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
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_confirmation_policy": "always_ask"}},
            )
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
            self.assertEqual(ready["permission_mode"], "auto")
            self.assertEqual(running.wait_job(ready["job_id"])["status"], "completed")
            selected_revision = ready["media_plan"]["selected_resources"][0]["revision"]
            updated = model_payload("Edited model", "editable.safetensors", operations=["generate", "inpaint"])
            self.assertEqual(
                running.client.put(f"/api/v1/media-catalog/resources/{model['id']}", json=updated).status_code,
                200,
            )
            completed = running.client.get(f"/api/v1/capability-requests/{ready['id']}").json()
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["media_plan"]["selected_resources"][0]["revision"], selected_revision)
            approval = running.client.post(f"/api/v1/capability-requests/{ready['id']}/approval")
            self.assertEqual(approval.status_code, 409, approval.text)

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
