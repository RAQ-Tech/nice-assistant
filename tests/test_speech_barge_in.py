"""Interrupting speech stops the work, not just the sound.

Stopping playback used to mute the browser and leave the provider generating
audio nobody would ever hear, then write that audio to disk and rotate the cache
to make room for it. On a machine that is also running a GPU, work nobody asked
for is not free. See ADR 0036.
"""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from app.speech_clients import AUDIO_CHUNK_BYTES, SpeechCancelled, kokoro_speech
from tests.support import TestApp


class SlowResponse:
    """A provider that answers in pieces, the way a real one does."""

    def __init__(self, chunks, *, on_read=None, content_type="audio/wav"):
        self.chunks = list(chunks)
        self.on_read = on_read
        self.headers = {"Content-Type": content_type}
        self.closed = False
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def close(self):
        self.closed = True

    def read(self, _size=None):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return self.chunks.pop(0) if self.chunks else b""


class CancellableReadTests(unittest.TestCase):
    def test_a_response_is_read_in_pieces_rather_than_all_at_once(self):
        response = SlowResponse([b"a" * 10, b"b" * 10])
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
            audio = kokoro_speech("hello", "af_heart", "wav", "http://voice.invalid:8880")

        self.assertEqual(audio, b"a" * 10 + b"b" * 10)
        # Reading the whole body in one call would mean an interruption is only
        # noticed once the provider has finished, which is the behaviour this
        # replaces.
        self.assertGreater(response.reads, 1)

    def test_reading_stops_the_moment_nobody_is_waiting(self):
        stop = threading.Event()
        response = SlowResponse([b"a" * 10] * 100, on_read=lambda count: stop.set() if count == 2 else None)
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
            with self.assertRaises(SpeechCancelled):
                kokoro_speech("hello", "af_heart", "wav", "http://voice.invalid:8880", cancelled=stop.is_set)

        # The rest of the provider's audio is never read, and the connection is
        # closed, which is what tells it to stop generating.
        self.assertLess(response.reads, 5)
        self.assertTrue(response.closed)

    def test_an_endless_provider_response_is_refused_rather_than_held(self):
        endless = SlowResponse([b"a" * AUDIO_CHUNK_BYTES] * 2000)
        with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=endless):
            with self.assertRaises(ValueError) as raised:
                kokoro_speech("hello", "af_heart", "wav", "http://voice.invalid:8880")
        self.assertIn("more audio than", str(raised.exception))


class InterruptedSynthesisTests(unittest.TestCase):
    def _ready(self, running) -> str:
        user_id = running.create_and_login()
        running.client.put(
            "/api/v1/settings",
            json={
                "tts_provider": "local",
                "tts_format": "wav",
                "preferences": {"tts_local_base_url": "http://127.0.0.1:8880"},
            },
        )
        return user_id

    def test_an_interrupted_synthesis_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            stop = threading.Event()
            response = SlowResponse([b"a" * 10] * 100, on_read=lambda count: stop.set() if count == 2 else None)
            with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
                with self.assertRaises(SpeechCancelled):
                    running.services.speech.synthesize(
                        user_id,
                        {"text": "a long reply nobody is listening to"},
                        cancelled=stop.is_set,
                    )

            # No file, no row, and no cache rotation to make room for audio
            # nobody will hear.
            self.assertEqual(list(running.config.audio_dir.glob("*")), [])

    def test_a_completed_synthesis_still_writes_its_audio(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            response = SlowResponse([b"a" * 10, b"b" * 10])
            with mock.patch("app.speech_clients.urllib.request.urlopen", return_value=response):
                result = running.services.speech.synthesize(user_id, {"text": "hello"})

            self.assertEqual(result["format"], "wav")
            stored = list(running.config.audio_dir.glob("*.wav"))
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].read_bytes(), b"a" * 10 + b"b" * 10)


if __name__ == "__main__":
    unittest.main()
