"""Asking for a picture of a message does not send the message to the image model.

The button that makes a picture from something the assistant said used to wrap
the message in an English instruction - "create a coherent image inspired by
this assistant response, preserve named people, places, objects, mood, and
visual style" - and post the whole thing as the prompt. Nothing downstream took
it back off. A diffusion model was handed the words "assistant response" and
several hundred words of prose, and asked to draw them.

The passage now goes to a task model that returns a typed scene, and the
existing dialect compiler renders that scene into whichever syntax the selected
checkpoint wants. These pin that, and pin the fallback: a scene task that cannot
run must leave a working picture behind, not a failed one.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.media_scene import EMPTY_SCENE
from app.prompt_dialect import compile_prompt
from app.task_contracts import (
    SCENE_FROM_MESSAGE,
    SceneFromMessageTaskInput,
    SceneFromMessageTaskOutput,
    TaskContractError,
    task_definition,
)
from tests.support import TestApp


PASSAGE = (
    "She had been putting it off for weeks, but that afternoon she finally carried the "
    "nipping press out into the garage and set it under the north window, where the light "
    "was good, and the cat immediately sat on it."
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.definition = task_definition(SCENE_FROM_MESSAGE)

    def test_the_model_is_asked_for_a_scene_and_nothing_else(self):
        schema = self.definition.schema(SceneFromMessageTaskInput(PASSAGE))

        # Prompt syntax belongs to the checkpoint. A role that could return
        # prompt text would be deciding something that is not its to decide.
        self.assertEqual(schema["required"], ["scene"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), {"scene"})

    def test_a_scene_survives_the_round_trip(self):
        parsed = self.definition.parse(
            '{"scene": {"subject": "a woman and a grey cat", "action": "setting down a press",'
            ' "setting": "a garage under a north window"}}',
            SceneFromMessageTaskInput(PASSAGE),
            384,
        )

        self.assertEqual(parsed.scene["subject"], "a woman and a grey cat")
        self.assertEqual(parsed.scene["setting"], "a garage under a north window")

    def test_a_scene_with_nothing_in_it_is_refused(self):
        # An empty scene renders to an empty prompt. Accepting one would produce
        # a picture of nothing and call it a success.
        with self.assertRaises(TaskContractError):
            self.definition.parse('{"scene": {"mood": "wistful"}}', SceneFromMessageTaskInput(PASSAGE), 384)

    def test_the_fallback_is_an_empty_scene_rather_than_a_failure(self):
        fallback = self.definition.fallback(SceneFromMessageTaskInput(PASSAGE))

        # An empty scene means the compiler uses the passage as written, which
        # is what happened before this role existed. A picture still arrives.
        self.assertEqual(fallback.scene, dict(EMPTY_SCENE))


class CompilerTests(unittest.TestCase):
    def test_a_scene_is_rendered_instead_of_the_passage(self):
        scene = {"subject": "a woman and a grey cat", "setting": "a garage under a north window"}

        compiled = compile_prompt(PASSAGE, None, scene=scene)

        self.assertTrue(compiled["from_scene"])
        self.assertIn("grey cat", compiled["positive"])
        # The prose the scene came from must not also be in the prompt.
        self.assertNotIn("putting it off for weeks", compiled["positive"])

    def test_without_a_scene_the_passage_is_still_used(self):
        compiled = compile_prompt(PASSAGE, None, scene=None)

        self.assertFalse(compiled["from_scene"])
        self.assertIn("nipping press", compiled["positive"])


class ThroughTheProductTests(unittest.TestCase):
    def _running(self, tmp):
        running = TestApp(Path(tmp)).__enter__()
        running.create_and_login()
        return running

    def _submit(self, running, scene_result, body=None):
        # A prompt deliberately disagreeing with the passage, because the two
        # arriving together is exactly when the rule has to be unambiguous.
        payload = {"prompt": "a red kite", "illustrate_text": PASSAGE, "provider": "disabled"}
        payload.update(body or {})
        with mock.patch.object(running.services.capabilities.task_models, "run", side_effect=scene_result):
            return running.client.post("/api/v1/media/image-jobs", json=payload)

    def test_the_scene_reaches_the_stored_request_and_the_passage_does_not(self):
        scene = {"subject": "a woman and a grey cat", "setting": "a garage under a north window"}
        outcome = mock.Mock(fallback_used=False, value=SceneFromMessageTaskOutput(scene))
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                accepted = self._submit(running, lambda *_a, **_k: outcome)
                self.assertEqual(accepted.status_code, 202, accepted.text)
                request = running.client.get(
                    f"/api/v1/capability-requests/{accepted.json()['capability_request_id']}"
                ).json()
            finally:
                running.__exit__(None, None, None)

        stored = dict(request["arguments"].get("scene") or {})
        self.assertEqual(stored.get("subject"), "a woman and a grey cat")

    def test_a_scene_task_that_cannot_run_still_produces_a_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                accepted = self._submit(running, lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no model")))

                # The passage becomes the prompt, which is exactly the behaviour
                # this replaced. A missing task model is not a missing picture.
                self.assertEqual(accepted.status_code, 202, accepted.text)
                request = running.client.get(
                    f"/api/v1/capability-requests/{accepted.json()['capability_request_id']}"
                ).json()
            finally:
                running.__exit__(None, None, None)

        self.assertFalse(request["arguments"].get("scene"))
        # The passage, not the prompt sent beside it. What somebody asked to see
        # is what they asked to see.
        self.assertIn("nipping press", str(request["arguments"].get("prompt")))
        self.assertNotIn("red kite", str(request["arguments"].get("prompt")))

    def test_a_typed_prompt_is_left_exactly_as_typed(self):
        ran = []

        def never(*args, **kwargs):
            ran.append(args)
            raise AssertionError("a typed prompt must not be rewritten")

        with tempfile.TemporaryDirectory() as tmp:
            running = self._running(tmp)
            try:
                with mock.patch.object(running.services.capabilities.task_models, "run", side_effect=never):
                    accepted = running.client.post(
                        "/api/v1/media/image-jobs",
                        json={"prompt": "a red kite over a beach", "provider": "disabled"},
                    )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                request = running.client.get(
                    f"/api/v1/capability-requests/{accepted.json()['capability_request_id']}"
                ).json()
            finally:
                running.__exit__(None, None, None)

        # Somebody who wrote a prompt meant it. Sending their words through a
        # model that rewrites them would be taking the request away from them.
        self.assertEqual(ran, [])
        self.assertEqual(request["arguments"]["prompt"], "a red kite over a beach")
        self.assertFalse(request["arguments"].get("scene"))


if __name__ == "__main__":
    unittest.main()
