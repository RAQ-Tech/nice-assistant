"""A persona can be several photos, used together.

One photo makes a likeness that carries the quirks of one shot - its angle, its
lighting, whatever the camera did that day. PhotoMaker stacks a batch into
something steadier. InstantID takes one and always will.

So a graph declares how many photos it can take by how many image inputs it
binds, a persona has however many it has, and these pin the reconciliation:
every photo lands in its own slot where there is one, repeats where there are
fewer, and the record of a picture names all of them.
"""

import json
from pathlib import Path
import unittest
from unittest import mock

from app.media_clients import comfyui_image
from app.provider_contracts import CancellationToken
from app.workflow_template import resolve_template
from tests.test_workflow_bindings import comfy_transport


class ReferenceSlotTests(unittest.TestCase):
    def _graph(self) -> dict:
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
            "2": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
            "3": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
            "41": {"class_type": "CLIPTextEncode", "inputs": {"text": "saved"}},
        }

    def _settings(self, paths, digests) -> dict:
        return {
            "additional_parameters": json.dumps(self._graph()),
            "prompt_bindings": [{"node_id": "41", "input_name": "text"}],
            "identity_image_bindings": [
                {"node_id": "1", "input_name": "image"},
                {"node_id": "2", "input_name": "image"},
                {"node_id": "3", "input_name": "image"},
            ],
            "identity_reference_paths": paths,
            "identity_reference_sha256s": digests,
        }

    def _run(self, settings) -> dict:
        captured = {}
        uploads = []

        def transport(request, timeout=0):
            if request.full_url.endswith("/upload/image"):
                uploads.append(request.data)
                # ComfyUI answers with the name it stored the image under.
                return _UploadResponse(f"stored-{len(uploads)}.png")
            return comfy_transport(captured)(request, timeout)

        with mock.patch("app.media_clients.urllib.request.urlopen", side_effect=transport):
            with mock.patch("app.media_clients.read_identity_image_file", side_effect=_fake_reference):
                comfyui_image("a kite", "512x512", "none", True, "http://c.invalid:8188", settings, CancellationToken())
        return captured["workflow"]

    def test_three_photos_fill_three_slots(self):
        workflow = self._run(self._settings(["a.png", "b.png", "c.png"], [_digest("a"), _digest("b"), _digest("c")]))

        # Each slot gets its own photo, which is what makes the likeness
        # steadier than any one shot.
        self.assertEqual(
            [workflow[node]["inputs"]["image"] for node in ("1", "2", "3")],
            ["stored-1.png", "stored-2.png", "stored-3.png"],
        )

    def test_one_photo_fills_every_slot(self):
        workflow = self._run(self._settings(["a.png"], [_digest("a")]))

        # Leaving a slot pointing at a file the provider does not have would
        # fail the whole graph. A repeat is harmless: the technique averages
        # them, so this is exactly the single-photo behaviour.
        self.assertEqual(
            [workflow[node]["inputs"]["image"] for node in ("1", "2", "3")],
            ["stored-1.png"] * 3,
        )

    def test_two_photos_repeat_from_the_start(self):
        workflow = self._run(self._settings(["a.png", "b.png"], [_digest("a"), _digest("b")]))

        self.assertEqual(
            [workflow[node]["inputs"]["image"] for node in ("1", "2", "3")],
            ["stored-1.png", "stored-2.png", "stored-1.png"],
        )

    def test_a_single_reference_workflow_is_unchanged(self):
        settings = self._settings(["a.png"], [_digest("a")])
        settings["identity_image_bindings"] = [{"node_id": "1", "input_name": "image"}]

        workflow = self._run(settings)

        self.assertEqual(workflow["1"]["inputs"]["image"], "stored-1.png")
        # Nothing was written into the slots this graph does not bind.
        self.assertEqual(workflow["2"]["inputs"]["image"], "placeholder.png")


class TemplateSlotTests(unittest.TestCase):
    def test_photomaker_takes_a_batch_and_instantid_takes_one(self):
        photomaker = resolve_template("photomaker-v2-sdxl")
        instantid = resolve_template("instantid-sdxl")

        self.assertEqual(len(photomaker["bindings"]["identity_image_bindings"]), 3)
        self.assertEqual(len(instantid["bindings"]["identity_image_bindings"]), 1)

    def test_every_photomaker_slot_reaches_the_encoder(self):
        template = resolve_template("photomaker-v2-sdxl")
        graph = template["workflow"]
        bound = {binding["node_id"] for binding in template["bindings"]["identity_image_bindings"]}

        # A slot nothing consumes would take a photo and quietly ignore it.
        reachable = set()
        frontier = [graph["4"]["inputs"]["image"][0]]
        while frontier:
            node_id = frontier.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for value in graph[node_id]["inputs"].values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    frontier.append(value[0])
        self.assertTrue(bound <= reachable, bound - reachable)


def _digest(seed: str) -> str:
    from hashlib import sha256

    return sha256(seed.encode()).hexdigest()


def _fake_reference(path, max_bytes=0):
    return Path(path).stem.encode()


class _UploadResponse:
    def __init__(self, name: str):
        self.name = name
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def close(self):
        return None

    def read(self, *_size):
        return json.dumps({"name": self.name, "subfolder": "", "type": "input"}).encode()


if __name__ == "__main__":
    unittest.main()
