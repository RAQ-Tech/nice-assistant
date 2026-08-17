"""Speech starts when the first audio exists, not when the last one does.

Synthesis had to finish before playback could begin, so the wait before a reply
was spoken was the wait for the whole reply to be generated. The completed file
is still written at the end - replay is unchanged - but it is no longer what the
listening waits on. See ADR 0037.
"""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from app.api_v1 import _streamed_audio
from app.provider_contracts import CancellationToken
from app.speech_clients import STREAMABLE_FORMATS, SpeechCancelled, kokoro_speech_stream
from app.service_errors import RequestError
from tests.support import TestApp
from tests.test_speech_barge_in import SlowResponse


class StreamingClientTests(unittest.TestCase):
    def test_audio_is_yielded_as_it_arrives(self):
        response = SlowResponse([b"one", b"two", b"three"], content_type="audio/mpeg")
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
            pieces = list(kokoro_speech_stream("hello", "af_heart", "mp3", "http://voice.invalid:8880"))

        # Three pieces, not one joined body: the first can be played while the
        # third is still being generated.
        self.assertEqual(pieces, [b"one", b"two", b"three"])

    def test_a_stream_asks_the_provider_to_stream(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            import json

            captured["payload"] = json.loads(request.data.decode())
            return SlowResponse([b"audio"], content_type="audio/mpeg")

        with mock.patch("app.speech_clients.urllib.request.urlopen", side_effect=fake_urlopen):
            list(kokoro_speech_stream("hello", "af_heart", "mp3", "http://voice.invalid:8880"))

        self.assertTrue(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["response_format"], "mp3")

    def test_a_provider_that_answers_with_json_is_refused_rather_than_played(self):
        response = SlowResponse([b'{"download_url": "/x.mp3"}'], content_type="application/json")
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
            with self.assertRaises(ValueError) as raised:
                list(kokoro_speech_stream("hello", "af_heart", "mp3", "http://voice.invalid:8880"))
        # The completed-file path follows a download link; a stream cannot, and
        # handing the browser a JSON body to play would be worse than failing.
        self.assertIn("not audio", str(raised.exception))

    def test_an_interrupted_stream_stops_reading(self):
        stop = threading.Event()
        response = SlowResponse(
            [b"a" * 10] * 100,
            on_read=lambda count: stop.set() if count == 2 else None,
            content_type="audio/mpeg",
        )
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
            with self.assertRaises(SpeechCancelled):
                list(kokoro_speech_stream("hi", "af_heart", "mp3", "http://v.invalid:8880", cancelled=stop.is_set))

        self.assertLess(response.reads, 5)
        self.assertTrue(response.closed)


class StreamingServiceTests(unittest.TestCase):
    def _ready(self, running, fmt="mp3") -> str:
        user_id = running.create_and_login()
        running.client.put(
            "/api/v1/settings",
            json={
                "tts_provider": "local",
                "tts_format": fmt,
                "preferences": {"tts_local_base_url": "http://127.0.0.1:8880"},
            },
        )
        return user_id

    def test_a_finished_stream_still_leaves_a_file_to_replay(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            response = SlowResponse([b"one", b"two"], content_type="audio/mpeg")
            with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
                audio_id, fmt, pieces = running.services.speech.stream_synthesis(user_id, {"text": "hello"})
                # The id is known before the first byte, so it can be named in a
                # header while the browser is still listening.
                self.assertTrue(audio_id)
                self.assertEqual(fmt, "mp3")
                self.assertEqual(list(pieces), [b"one", b"two"])

            stored = running.config.audio_dir / f"{audio_id}.mp3"
            self.assertTrue(stored.exists())
            self.assertEqual(stored.read_bytes(), b"onetwo")

    def test_an_abandoned_stream_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            response = SlowResponse([b"a" * 10] * 100, content_type="audio/mpeg")
            with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
                _audio_id, _fmt, pieces = running.services.speech.stream_synthesis(user_id, {"text": "hello"})
                iterator = iter(pieces)
                next(iterator)
                # The browser walked away, so the rest of this is never pulled.
                iterator.close()

            self.assertEqual(list(running.config.audio_dir.glob("*")), [])

    def test_a_format_that_cannot_start_early_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running, fmt="wav")
            with self.assertRaises(RequestError) as raised:
                running.services.speech.stream_synthesis(user_id, {"text": "hello"})

            # WAV carries its length in a header nothing can fill in halfway
            # through, so saying "streaming is on" for it would be a lie.
            message = str(raised.exception)
            self.assertIn("wav", message)
            for streamable in STREAMABLE_FORMATS:
                self.assertIn(streamable, message)


class StreamedBodyTests(unittest.IsolatedAsyncioTestCase):
    """The wrapper that hands provider pieces to the browser.

    The response body itself is not asserted through the test client. Starlette
    only streams directly on ASGI spec 2.4 and above; below that it races the
    body against a disconnect listener, and the test client queues a disconnect
    as soon as the request body has been read, so the race is always lost. That
    is a property of the harness rather than of the deployment, and asserting an
    empty body there would pin the harness rather than this code.
    """

    async def _collect(self, pieces, token):
        return [piece async for piece in _streamed_audio(pieces, token)]

    async def test_pieces_reach_the_browser_one_at_a_time(self):
        token = CancellationToken()

        def pieces():
            yield b"one"
            yield b"two"

        self.assertEqual(await self._collect(pieces(), token), [b"one", b"two"])

    async def test_walking_away_trips_the_token_and_closes_the_provider(self):
        token = CancellationToken()
        closed = []

        def pieces():
            try:
                while True:
                    yield b"a"
            finally:
                closed.append(True)

        body = _streamed_audio(pieces(), token)
        self.assertEqual(await anext(body), b"a")
        await body.aclose()

        # The token is what the still-blocked provider read checks at its next
        # piece, and closing the iterator ends the provider connection.
        self.assertTrue(token.cancelled)
        self.assertEqual(closed, [True])


class StreamingRouteTests(unittest.TestCase):
    def test_the_route_names_the_recording_before_it_sends_the_audio(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={
                    "tts_provider": "local",
                    "tts_format": "mp3",
                    "preferences": {"tts_local_base_url": "http://127.0.0.1:8880"},
                },
            )
            response = SlowResponse([b"one", b"two"], content_type="audio/mpeg")
            with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
                streamed = running.client.post("/api/v1/speech/streams", json={"text": "hello"})

            self.assertEqual(streamed.status_code, 200, streamed.text)
            self.assertEqual(streamed.headers["content-type"], "audio/mpeg")
            # The id goes out before the first byte, so the browser can register
            # the recording for replay while it is still listening to it.
            self.assertTrue(streamed.headers["x-nice-assistant-audio-id"])

    def test_the_route_refuses_a_format_that_cannot_start_early(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"tts_provider": "local", "tts_format": "wav", "preferences": {}},
            )
            refused = running.client.post("/api/v1/speech/streams", json={"text": "hello"})

            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("mp3", refused.text)


if __name__ == "__main__":
    unittest.main()
