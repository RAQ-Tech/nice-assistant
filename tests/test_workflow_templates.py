"""Shipped ComfyUI graphs, with their bindings declared rather than discovered.

Guided setup used to ask a person to export a graph in API format and choose
which of its inputs receives the prompt and which receives the reference. These
tests pin the other direction: the graph ships, its bindings are written with
it, and inspection answers whether this installation can run it.

They also pin what a template must not do - claim it has been run here, or imply
it checked an asset that nothing can see. See ADR 0030 and ADR 0031.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.media_catalog_service import MediaCatalogService
from app.service_errors import RequestError
from app.workflow_template import (
    MODEL_ARCHITECTURES,
    available_templates,
    normalize_template,
    resolve_template,
    template_default_settings,
)
from tests.support import TestApp
from tests.test_workflow_bindings import Response


class ShippedTemplateTests(unittest.TestCase):
    def test_every_shipped_template_normalizes(self):
        templates = available_templates()

        self.assertTrue(templates)
        for template in templates:
            self.assertIn(template["kind"], ("image", "video"))
            if template["kind"] == "image":
                self.assertIn(template["mechanism"], ("reference_adapter", "identity_pass"))
            else:
                # A clip has no face to condition.
                self.assertIsNone(template["mechanism"])
            self.assertTrue(set(template["architectures"]) <= set(MODEL_ARCHITECTURES))
            self.assertTrue(template["summary"], template["id"])
            self.assertTrue(template["required_assets"], template["id"])

    def test_every_shipped_binding_targets_an_input_of_its_own_graph(self):
        for template in available_templates():
            settings = template_default_settings(template)
            # Validated by the same code that validates an operator's own
            # bindings, so a template cannot ship something a person could not
            # have saved by hand.
            provider = "local-video" if template["kind"] == "video" else "local-image"
            normalized = MediaCatalogService._normalize_default_settings("workflow", provider, "comfyui", settings)
            if template["kind"] == "video":
                # A clip is made from the prompt and the paired model; there is
                # no reference to receive.
                self.assertFalse(normalized.get("identity_image_bindings"), template["id"])
                self.assertTrue(normalized["checkpoint_bindings"], template["id"])
            else:
                self.assertTrue(normalized["identity_image_bindings"], template["id"])
            # A graph that renders from a prompt must be able to receive the
            # request. One that only changes a picture it is handed says so,
            # and binding a prompt into its face-index widget would be worse
            # than having no binding at all.
            if template["consumes_prompt"]:
                self.assertTrue(normalized["prompt_bindings"], template["id"])
            else:
                self.assertFalse(normalized["prompt_bindings"], template["id"])
                self.assertTrue(normalized["source_image_bindings"], template["id"])

    def test_a_reference_binding_targets_the_node_that_loads_the_image(self):
        for template in available_templates():
            graph = template["workflow"]
            for binding in template["bindings"].get("identity_image_bindings", []):
                node = graph[binding["node_id"]]
                # The executor writes an uploaded filename, which only means
                # something on a loader. Writing it into the identity node's
                # own IMAGE input would replace a link with a string.
                self.assertEqual(node["class_type"], "LoadImage", template["id"])

    def test_a_pass_over_a_finished_picture_declares_what_it_receives(self):
        template = resolve_template("reactor-face-swap")

        self.assertEqual(template["mechanism"], "identity_pass")
        self.assertEqual(template["operations"], ["image_to_image"])
        self.assertFalse(template["consumes_prompt"])
        # It is handed the previous pass's picture and the approved reference,
        # and both must land on the nodes that load them.
        self.assertTrue(template["bindings"]["source_image_bindings"])
        self.assertTrue(template["bindings"]["identity_image_bindings"])
        for role in ("source_image_bindings", "identity_image_bindings"):
            for binding in template["bindings"][role]:
                self.assertEqual(template["workflow"][binding["node_id"]]["class_type"], "LoadImage")

    def test_a_template_that_needs_a_trigger_word_ships_a_prefix_containing_it(self):
        for template in available_templates():
            if not template["required_prompt_token"]:
                continue
            self.assertIn(template["required_prompt_token"], template["prompt_prefix"], template["id"])
            settings = template_default_settings(template)
            self.assertEqual(settings["required_prompt_token"], template["required_prompt_token"])

    def test_a_template_promising_a_word_it_cannot_supply_is_refused(self):
        base = json.loads(Path("assets/workflow-templates/photomaker-v2-sdxl.json").read_text(encoding="utf-8"))
        with self.assertRaises(RequestError) as raised:
            normalize_template({**base, "prompt_prefix": "a portrait of a person"})
        self.assertIn("photomaker", str(raised.exception))

    def test_a_template_that_cannot_receive_the_prompt_is_refused(self):
        base = json.loads(Path("assets/workflow-templates/instantid-sdxl.json").read_text(encoding="utf-8"))
        bindings = {key: value for key, value in base["bindings"].items() if key != "prompt_bindings"}
        with self.assertRaises(RequestError) as raised:
            normalize_template({**base, "bindings": bindings})
        self.assertIn("prompt", str(raised.exception))

    def test_resolving_an_unknown_template_is_a_not_found(self):
        from app.service_errors import NotFoundError

        with self.assertRaises(NotFoundError):
            resolve_template("no-such-template")


class ModelFixture:
    def _model(self, running, architecture: str = "") -> dict:
        settings = {"architecture": architecture} if architecture else {}
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "image",
                "name": "Photoreal base",
                "provider_key": "local-image",
                "backend": "comfyui",
                "external_id": "photoreal.safetensors",
                "operations": ["generate"],
                "default_settings": settings,
            },
        )
        assert created.status_code == 201, created.text
        return created.json()


class TemplateOfferTests(ModelFixture, unittest.TestCase):
    def test_a_model_declares_the_family_it_belongs_to(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")

            self.assertEqual(model["default_settings"]["architecture"], "sdxl")

    def test_an_unknown_family_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            refused = running.client.post(
                "/api/v1/media-catalog/resources",
                json={
                    "resource_type": "model",
                    "kind": "image",
                    "name": "Mystery base",
                    "provider_key": "local-image",
                    "backend": "comfyui",
                    "external_id": "mystery.safetensors",
                    "operations": ["generate"],
                    "default_settings": {"architecture": "sdxl-turbo-maybe"},
                },
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("sdxl", refused.text)

    def test_templates_are_offered_against_a_declared_family(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "pony")
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()

            self.assertEqual(listed["model_architecture"], "pony")
            by_id = {item["id"]: item for item in listed["templates"]}
            # SDXL templates operate on the SDXL text encoder, which Pony
            # retrains. They are shown and marked, not hidden: the operator may
            # know something the declaration does not.
            self.assertFalse(by_id["photomaker-v2-sdxl"]["architecture_matches"])
            self.assertFalse(by_id["instantid-sdxl"]["architecture_matches"])
            # A pass over a finished picture never touches the text encoder, so
            # the family it was rendered with does not matter.
            self.assertTrue(by_id["reactor-face-swap"]["architecture_matches"])

    def test_an_undeclared_family_is_a_prompt_to_record_one_rather_than_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running)
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()

            self.assertEqual(listed["model_architecture"], "")
            self.assertTrue(all(item["architecture_matches"] for item in listed["templates"]))


class TemplateInstallTests(ModelFixture, unittest.TestCase):
    def _object_info(self, request, timeout=0):
        if request.full_url.endswith("/object_info"):
            return Response({"CheckpointLoaderSimple": {"input": {"required": {}}, "output": ["MODEL"]}})
        raise AssertionError(request.full_url)

    def _install(self, running, model_id: str, template_id: str = "photomaker-v2-sdxl"):
        return running.client.post(
            f"/api/v1/media-catalog/workflow-templates/{template_id}/installations",
            json={"model_id": model_id},
        )

    def test_installing_writes_a_bound_workflow_paired_with_the_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            created = self._install(running, model["id"])

            self.assertEqual(created.status_code, 201, created.text)
            resource = created.json()
            self.assertEqual(resource["compatible_model_ids"], [model["id"]])
            self.assertEqual(resource["features"], ["identity_control"])
            self.assertFalse(resource["needs_binding_review"])
            settings = resource["default_settings"]
            self.assertTrue(settings["prompt_bindings"])
            self.assertTrue(settings["identity_image_bindings"])
            # The graph takes the paired model rather than the placeholder
            # checkpoint name written into the shipped file.
            self.assertTrue(settings["checkpoint_bindings"])
            self.assertIn("not been generation-tested here", resource["notes"])

    def test_the_installed_graph_records_where_it_came_from(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            resource = self._install(running, model["id"]).json()

            self.assertEqual(resource["source_template_id"], "photomaker-v2-sdxl")
            self.assertEqual(resource["source_template_version"], 1)
            listed = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": model["id"]}
            ).json()
            entry = next(item for item in listed["templates"] if item["id"] == "photomaker-v2-sdxl")
            self.assertEqual(entry["installed_resource_id"], resource["id"])
            # Same version, so there is nothing to offer.
            self.assertFalse(entry["update_available"])

    def test_the_graph_is_pointed_at_the_file_this_installation_has(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            created = running.client.post(
                "/api/v1/media-catalog/workflow-templates/instantid-sdxl/installations",
                json={
                    "model_id": model["id"],
                    # An InstantID ControlNet downloads under whichever name its
                    # source used. Renaming a file to match a graph is the hand
                    # editing templates exist to remove.
                    "asset_choices": [
                        {"node_id": "4", "input_name": "control_net_name", "value": "my-instantid-cn.safetensors"}
                    ],
                },
            )

            self.assertEqual(created.status_code, 201, created.text)
            graph = created.json()["default_settings"]["workflow_patch"]
            self.assertEqual(graph["4"]["inputs"]["control_net_name"], "my-instantid-cn.safetensors")
            # Nothing else moved.
            self.assertEqual(graph["2"]["inputs"]["instantid_file"], "ip-adapter.bin")

    def test_a_choice_must_name_a_file_input_of_this_graph(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            refused = running.client.post(
                "/api/v1/media-catalog/workflow-templates/instantid-sdxl/installations",
                json={
                    "model_id": model["id"],
                    "asset_choices": [{"node_id": "8", "input_name": "weight", "value": "0.9"}],
                },
            )

            # Node 8 exists but `weight` is a float, not a file name. A choice
            # is for pointing at a file, not for editing the graph.
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("file input", refused.text)

    def test_a_template_is_refused_for_a_family_it_was_not_built_for(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "flux")
            refused = self._install(running, model["id"])

            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("flux", refused.text)

    def test_installing_twice_gives_two_graphs_rather_than_overwriting_one(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            model = self._model(running, "sdxl")
            first = self._install(running, model["id"]).json()
            second = self._install(running, model["id"])

            # An operator may have tuned the first one. Nothing here rewrites
            # a graph somebody is already using.
            self.assertEqual(second.status_code, 201, second.text)
            self.assertNotEqual(second.json()["id"], first["id"])

    def test_verification_asks_the_provider_rather_than_asserting_it_works(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            with mock.patch("app.providers.urllib.request.urlopen", side_effect=self._object_info):
                verified = running.client.post("/api/v1/media-catalog/workflow-templates/photomaker-v2-sdxl/verify")

            self.assertEqual(verified.status_code, 200, verified.text)
            body = verified.json()
            # This ComfyUI reports one of the template's node types, so the
            # rest are named as missing rather than the template being called
            # compatible.
            self.assertFalse(body["provider_compatible"])
            self.assertIn("PhotoMakerEncodeV2", body["missing_node_types"])
            # A graph whose node types are missing has no asset checks to make,
            # which is a different answer from "the file is not there".
            self.assertEqual(body["asset_checks"], [])

    def test_verifying_an_unknown_template_is_a_not_found(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            missing = running.client.post("/api/v1/media-catalog/workflow-templates/nope/verify")

            self.assertEqual(missing.status_code, 404, missing.text)


# A ComfyUI that has every node the Wan graph needs - they ship with ComfyUI -
# but has downloaded a different quantisation of the model than the one the
# template names. What the check must say is the file, and what it has instead.
def _combo(*values):
    return [list(values)]


WAN_OBJECT_INFO = {
    "UNETLoader": {
        "input": {
            "required": {
                "unet_name": _combo("wan2.2_ti2v_5B_fp8.safetensors"),
                "weight_dtype": _combo("default", "fp8_e4m3fn"),
            }
        },
        "output": ["MODEL"],
    },
    "CLIPLoader": {
        "input": {
            "required": {
                "clip_name": _combo("umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
                "type": _combo("stable_diffusion", "wan"),
            },
            "optional": {"device": _combo("default", "cpu")},
        },
        "output": ["CLIP"],
    },
    "VAELoader": {
        "input": {"required": {"vae_name": _combo("wan2.2_vae.safetensors")}},
        "output": ["VAE"],
    },
    "ModelSamplingSD3": {
        "input": {"required": {"model": ["MODEL"], "shift": ["FLOAT", {"default": 3.0}]}},
        "output": ["MODEL"],
    },
    "CLIPTextEncode": {
        "input": {"required": {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]}},
        "output": ["CONDITIONING"],
    },
    "Wan22ImageToVideoLatent": {
        "input": {
            "required": {
                "vae": ["VAE"],
                "width": ["INT", {"default": 1280}],
                "height": ["INT", {"default": 704}],
                "length": ["INT", {"default": 49}],
                "batch_size": ["INT", {"default": 1}],
            },
            "optional": {"start_image": ["IMAGE"]},
        },
        "output": ["LATENT"],
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL"],
                "seed": ["INT", {"default": 0}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 8.0}],
                "sampler_name": _combo("uni_pc", "euler"),
                "scheduler": _combo("simple", "normal"),
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "denoise": ["FLOAT", {"default": 1.0}],
            }
        },
        "output": ["LATENT"],
    },
    "VAEDecode": {
        "input": {"required": {"samples": ["LATENT"], "vae": ["VAE"]}},
        "output": ["IMAGE"],
    },
    "CreateVideo": {
        "input": {
            "required": {"images": ["IMAGE"], "fps": ["FLOAT", {"default": 30.0}]},
            "optional": {"audio": ["AUDIO"]},
        },
        "output": ["VIDEO"],
    },
    "SaveVideo": {
        "input": {
            "required": {
                "video": ["VIDEO"],
                "filename_prefix": ["STRING", {"default": "video/ComfyUI"}],
                "format": _combo("auto", "mp4"),
            },
            "optional": {"codec": _combo("auto", "h264")},
        },
        "output": [],
        "output_node": True,
    },
}


class VideoTemplateTests(ModelFixture, unittest.TestCase):
    """The shipped Wan 2.2 graph: a clip from the prompt, offered to video models only."""

    def _video_model(self, running) -> dict:
        created = running.client.post(
            "/api/v1/media-catalog/resources",
            json={
                "resource_type": "model",
                "kind": "video",
                "name": "Wan 2.2 5B",
                "provider_key": "local-video",
                "backend": "comfyui",
                "external_id": "wan2.2_ti2v_5B_fp16.safetensors",
                "operations": ["generate"],
                "default_settings": {"architecture": "wan"},
            },
        )
        assert created.status_code == 201, created.text
        return created.json()

    def test_the_graph_is_bound_by_construction_and_carries_its_own_size(self):
        template = resolve_template("wan22-ti2v-5b")

        self.assertEqual(template["kind"], "video")
        self.assertIsNone(template["mechanism"])
        self.assertEqual(template["features"], ["text_to_video"])
        self.assertEqual(template["architectures"], ["wan"])
        bindings = template["bindings"]
        graph = template["workflow"]
        for role in ("prompt_bindings", "negative_prompt_bindings", "seed_bindings", "checkpoint_bindings"):
            self.assertTrue(bindings[role], role)
        # The paired model lands on the diffusion-model loader, which is what
        # `unet_name` is to a Wan graph.
        checkpoint = bindings["checkpoint_bindings"][0]
        self.assertEqual(graph[checkpoint["node_id"]]["class_type"], "UNETLoader")
        self.assertEqual(checkpoint["input_name"], "unet_name")
        for role in ("prompt_bindings", "negative_prompt_bindings"):
            self.assertEqual(graph[bindings[role][0]["node_id"]]["class_type"], "CLIPTextEncode")
        # Size and length are the model's own and are deliberately not bound:
        # the request's picture size is not a clip size.
        latent = next(node for node in graph.values() if node["class_type"] == "Wan22ImageToVideoLatent")
        self.assertEqual(
            (latent["inputs"]["width"], latent["inputs"]["height"], latent["inputs"]["length"]),
            (1280, 704, 121),
        )
        self.assertNotIn("width_bindings", bindings)
        self.assertTrue(any(node["class_type"] == "SaveVideo" for node in graph.values()))

    def test_a_video_graph_may_not_claim_an_identity_mechanism(self):
        base = json.loads(Path("assets/workflow-templates/wan22-ti2v-5b.json").read_text(encoding="utf-8"))
        with self.assertRaises(RequestError) as raised:
            normalize_template({**base, "mechanism": "reference_adapter"})
        self.assertIn("video", str(raised.exception))
        with self.assertRaises(RequestError):
            normalize_template({**base, "kind": "clip"})

    def test_video_graphs_are_offered_to_video_models_and_picture_graphs_to_picture_models(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            picture = self._model(running, "sdxl")
            video = self._video_model(running)
            for_picture = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": picture["id"]}
            ).json()
            for_video = running.client.get(
                "/api/v1/media-catalog/workflow-templates", params={"model_id": video["id"]}
            ).json()

            self.assertEqual(for_picture["model_kind"], "image")
            self.assertNotIn("wan22-ti2v-5b", {item["id"] for item in for_picture["templates"]})
            self.assertEqual(for_video["model_kind"], "video")
            self.assertEqual([item["id"] for item in for_video["templates"]], ["wan22-ti2v-5b"])
            self.assertTrue(for_video["templates"][0]["architecture_matches"])
            self.assertIsNone(for_video["templates"][0]["mechanism"])

    def test_a_graph_is_refused_for_a_model_that_makes_something_else(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            picture = self._model(running, "sdxl")
            video = self._video_model(running)
            clip_on_picture = running.client.post(
                "/api/v1/media-catalog/workflow-templates/wan22-ti2v-5b/installations",
                json={"model_id": picture["id"]},
            )
            picture_on_clip = running.client.post(
                "/api/v1/media-catalog/workflow-templates/photomaker-v2-sdxl/installations",
                json={"model_id": video["id"]},
            )

            self.assertEqual(clip_on_picture.status_code, 400, clip_on_picture.text)
            self.assertIn("video clips", clip_on_picture.text)
            self.assertEqual(picture_on_clip.status_code, 400, picture_on_clip.text)
            self.assertIn("pictures", picture_on_clip.text)

    def test_installing_pairs_the_clip_graph_with_the_video_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            video = self._video_model(running)
            created = running.client.post(
                "/api/v1/media-catalog/workflow-templates/wan22-ti2v-5b/installations",
                json={"model_id": video["id"]},
            )

            self.assertEqual(created.status_code, 201, created.text)
            resource = created.json()
            self.assertEqual(resource["kind"], "video")
            self.assertEqual(resource["provider_key"], "local-video")
            self.assertEqual(resource["features"], ["text_to_video"])
            self.assertEqual(resource["compatible_model_ids"], [video["id"]])
            settings = resource["default_settings"]
            self.assertEqual(settings["checkpoint_bindings"][0]["input_name"], "unet_name")
            self.assertTrue(settings["prompt_bindings"])
            self.assertFalse(settings.get("identity_image_bindings"))
            self.assertIn("not been generation-tested here", resource["notes"])

    def test_verification_names_the_model_this_comfyui_has_not_downloaded(self):
        def object_info(request, timeout=0):
            if request.full_url.endswith("/object_info"):
                return Response(WAN_OBJECT_INFO)
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"preferences": {"image_provider": "local", "image_local_backend": "comfyui"}},
            )
            with mock.patch("app.providers.urllib.request.urlopen", side_effect=object_info):
                verified = running.client.post("/api/v1/media-catalog/workflow-templates/wan22-ti2v-5b/verify")

            self.assertEqual(verified.status_code, 200, verified.text)
            body = verified.json()
            # Every node type is installed - they ship with ComfyUI - so the
            # answer is about the files: the one that is missing, by name, and
            # the files ComfyUI does have for that input.
            self.assertEqual(body["missing_node_types"], [])
            missing = [item for item in body["asset_checks"] if not item["available"]]
            self.assertEqual(
                [(item["node_type"], item["input_name"], item["value"]) for item in missing],
                [("UNETLoader", "unet_name", "wan2.2_ti2v_5B_fp16.safetensors")],
            )
            self.assertEqual(missing[0]["options"], ["wan2.2_ti2v_5B_fp8.safetensors"])
            self.assertFalse(body["provider_compatible"])
            # A video graph is never asked for an identity path.
            self.assertNotIn("reference-conditioned", body["message"])


if __name__ == "__main__":
    unittest.main()
