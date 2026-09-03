"""Speaking while the reply is still being written, on the server side.

Each finished sentence is spoken as its own request, the requests belong to
one session, and the session becomes one recording when the reply is done.
See ADR 0042.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.service_errors import NotFoundError, RequestError
from tests.support import TestApp
from tests.test_speech_barge_in import SlowResponse


class SpeechSessionTests(unittest.TestCase):
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

    def test_pieces_are_spoken_as_they_come_and_stored_once(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            speech = running.services.speech
            begun = speech.begin_speech_session(user_id, {})
            self.assertEqual(begun["format"], "mp3")

            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                return_value=SlowResponse([b"one", b"two"], content_type="audio/mpeg"),
            ):
                fmt, pieces = speech.stream_session_piece(user_id, begun["session_id"], "Hello there.")
                self.assertEqual(fmt, "mp3")
                # Streamed, not joined: the first piece can play while the
                # second is still being made.
                self.assertEqual(list(pieces), [b"one", b"two"])
            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                return_value=SlowResponse([b"three"], content_type="audio/mpeg"),
            ):
                _fmt, pieces = speech.stream_session_piece(user_id, begun["session_id"], "And a little more.")
                self.assertEqual(list(pieces), [b"three"])
            # Nothing is stored until the reply is finished.
            self.assertEqual(list(running.config.audio_dir.glob("*")), [])

            stored = speech.finish_speech_session(user_id, begun["session_id"])
            self.assertEqual(stored["audio_id"], begun["audio_id"])
            recording = running.config.audio_dir / f"{stored['audio_id']}.mp3"
            self.assertEqual(recording.read_bytes(), b"onetwothree")
            # Replay plays the whole reply, once, from the stored recording.
            self.assertEqual(running.client.get(f"/api/v1/audio/{stored['audio_id']}").status_code, 200)
            # A finished session is gone; finishing twice stores nothing twice.
            with self.assertRaises(NotFoundError):
                speech.finish_speech_session(user_id, begun["session_id"])

    def test_a_piece_cut_short_abandons_the_session_and_stores_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            speech = running.services.speech
            begun = speech.begin_speech_session(user_id, {})
            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                return_value=SlowResponse([b"a" * 10] * 50, content_type="audio/mpeg"),
            ):
                _fmt, pieces = speech.stream_session_piece(user_id, begun["session_id"], "A long sentence.")
                iterator = iter(pieces)
                next(iterator)
                # The person stopped it: the rest is never pulled.
                iterator.close()
            with self.assertRaises(RequestError) as refused:
                speech.stream_session_piece(user_id, begun["session_id"], "Another sentence.")
            self.assertEqual(refused.exception.status_code, 409)
            with self.assertRaises(RequestError):
                speech.finish_speech_session(user_id, begun["session_id"])
            self.assertEqual(list(running.config.audio_dir.glob("*")), [])

    def test_a_session_belongs_to_the_person_who_began_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            begun = running.services.speech.begin_speech_session(user_id, {})
            with self.assertRaises(NotFoundError):
                running.services.speech.stream_session_piece("somebody-else", begun["session_id"], "Hello.")
            with self.assertRaises(NotFoundError):
                running.services.speech.finish_speech_session("somebody-else", begun["session_id"])

    def test_a_format_that_cannot_start_early_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running, fmt="wav")
            with self.assertRaises(RequestError) as raised:
                running.services.speech.begin_speech_session(user_id, {})
            self.assertIn("wav", str(raised.exception))

    def test_the_routes_carry_a_session_from_beginning_to_recording(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            begun = running.client.post("/api/v1/speech/sessions", json={})
            self.assertEqual(begun.status_code, 200, begun.text)
            session_id = begun.json()["session_id"]
            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                return_value=SlowResponse([b"one"], content_type="audio/mpeg"),
            ):
                piece = running.client.post(
                    f"/api/v1/speech/sessions/{session_id}/pieces", json={"text": "Hello there."}
                )
            self.assertEqual(piece.status_code, 200, piece.text)
            self.assertEqual(piece.headers["content-type"], "audio/mpeg")
            # The body is not pulled through the test client (see
            # StreamedBodyTests in test_speech_streaming for why), so the
            # piece that reaches the recording is spoken through the service.
            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                return_value=SlowResponse([b"two"], content_type="audio/mpeg"),
            ):
                _fmt, pieces = running.services.speech.stream_session_piece(user_id, session_id, "And more.")
                self.assertEqual(list(pieces), [b"two"])
            finished = running.client.post(f"/api/v1/speech/sessions/{session_id}/finish")
            self.assertEqual(finished.status_code, 200, finished.text)
            self.assertEqual(finished.json()["audio_id"], begun.json()["audio_id"])

            gone = running.client.delete(f"/api/v1/speech/sessions/{session_id}")
            self.assertEqual(gone.status_code, 200)

    def test_abandoning_a_session_is_quiet_and_final(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = self._ready(running)
            speech = running.services.speech
            begun = speech.begin_speech_session(user_id, {})
            speech.abandon_speech_session(user_id, begun["session_id"])
            # Twice is fine: a stop that races the reply's end must not fail.
            speech.abandon_speech_session(user_id, begun["session_id"])
            with self.assertRaises(NotFoundError):
                speech.finish_speech_session(user_id, begun["session_id"])


if __name__ == "__main__":
    unittest.main()
