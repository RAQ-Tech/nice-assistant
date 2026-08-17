"""A spoken turn can finish without leaving this network.

Transcription was OpenAI-only: the service refused every other provider with a
501, so holding the microphone button sent audio off the machine or did nothing.
That was the one place the product contradicted what it says it is for.

The local path talks to a self-hosted Whisper service in the shape OpenAI
documents, because that is what the self-hosted servers implement. These pin the
two claims that matter - the request goes where the operator said and carries no
credential, and a spoken turn completes with nothing leaving the network.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.speech_clients import local_stt, normalized_stt_base_url
from tests.support import TestApp


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_size):
        return self.body


class LocalTranscriptionClientTests(unittest.TestCase):
    def _call(self, body: bytes, **kwargs) -> tuple[dict, object]:
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _Response(body)

        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "a.wav"
            audio.write_bytes(b"RIFFfake")
            with mock.patch("app.speech_clients.urllib.request.urlopen", side_effect=fake_urlopen):
                result = local_stt(str(audio), "http://127.0.0.1:8000", **kwargs)
        return result, captured["request"]

    def test_the_recording_goes_where_the_operator_said(self):
        _, request = self._call(b'{"text": "hello"}')

        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/audio/transcriptions")

    def test_no_credential_is_sent_to_a_service_on_this_network(self):
        _, request = self._call(b'{"text": "hello"}')

        # A key belongs to somebody else's service. Sending one to a box on the
        # LAN would leak it to whatever is listening there.
        self.assertNotIn("Authorization", {name.title() for name in request.headers})

    def test_the_model_and_language_reach_the_service(self):
        _, request = self._call(b'{"text": "hi"}', model="Systran/faster-whisper-small", language="es")

        body = request.data
        self.assertIn(b"Systran/faster-whisper-small", body)
        self.assertIn(b'name="language"', body)
        self.assertIn(b"es", body)

    def test_automatic_language_is_not_sent_as_a_language(self):
        _, request = self._call(b'{"text": "hi"}', language="auto")

        # "auto" is this product's word for saying nothing, not a language code
        # any transcription service would recognise.
        self.assertNotIn(b'name="language"', request.data)

    def test_a_plain_text_answer_is_still_a_transcript(self):
        # whisper.cpp's server answers text/plain unless asked otherwise.
        result, _ = self._call(b"  hello there  ")

        self.assertEqual(result["text"], "hello there")
        self.assertIsNone(result["language"])

    def test_an_unexpected_shape_transcribes_to_nothing_rather_than_raising(self):
        result, _ = self._call(b"[1, 2, 3]")

        self.assertEqual(result["text"], "")

    def test_an_empty_address_falls_back_to_the_documented_default(self):
        self.assertEqual(normalized_stt_base_url(""), "http://127.0.0.1:8000")
        self.assertEqual(normalized_stt_base_url("http://box:9000/"), "http://box:9000")


class LocalTranscriptionServiceTests(unittest.TestCase):
    """Through the wire, because the wire is where the 501 used to be."""

    def _transcribe(self, running: TestApp, body: bytes) -> tuple[object, object]:
        captured = {}

        def fake_run(command, **_kwargs):
            # ffmpeg is a real dependency of this path and not what is under
            # test; the conversion is stood in for so the request is not.
            Path(command[-1]).write_bytes(b"RIFFfake")
            return mock.Mock(returncode=0)

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _Response(body)

        with mock.patch("app.speech_service.subprocess.run", side_effect=fake_run):
            with mock.patch("app.speech_clients.urllib.request.urlopen", side_effect=fake_urlopen):
                response = running.client.post(
                    "/api/v1/speech/transcriptions",
                    files={"file": ("turn.webm", b"webm bytes", "audio/webm")},
                )
        return response, captured.get("request")

    def test_a_spoken_turn_completes_with_no_api_key_configured(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {"stt_local_base_url": "http://127.0.0.1:8000"},
                },
            )

            response, request = self._transcribe(running, b'{"text": "what do I drive"}')

            # The cloud path refuses without a key. Requiring one for a service
            # on your own network would make the local option unusable for the
            # person who wants it most.
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["text"], "what do I drive")
            self.assertTrue(request.full_url.startswith("http://127.0.0.1:8000"))
            self.assertNotIn("Authorization", {name.title() for name in request.headers})

    def test_the_configured_model_is_what_gets_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {
                        "stt_local_base_url": "http://127.0.0.1:8000",
                        "stt_model_local": "large-v3",
                    },
                },
            )

            _, request = self._transcribe(running, b'{"text": "ok"}')

        self.assertIn(b"large-v3", request.data)

    def test_an_address_outside_the_private_lan_policy_is_refused_at_save(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()

            response = running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {"stt_local_base_url": "http://somebody-elses-box.example.com"},
                },
            )

            # "Local" has to mean local. Without this, pointing the setting at a
            # host on the internet would be a quiet way to send every recording
            # off the machine under a label that says it does not.
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("private-LAN", response.json()["error"]["message"])


class LocalityTests(unittest.TestCase):
    def test_the_homepage_says_a_recording_stays_here(self):
        from app.data_locality import conversation_locality

        summary = conversation_locality({"stt_provider": "local", "tts_provider": "local"}, "ollama", True)

        spoken = next(part for part in summary["parts"] if part["label"] == "What you say")
        self.assertEqual(spoken["locality"], "local")
        self.assertTrue(summary["everything_local"])


if __name__ == "__main__":
    unittest.main()
