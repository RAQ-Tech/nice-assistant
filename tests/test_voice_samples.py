"""Choosing a voice by hearing it. See ADR 0051.

A voice was a name typed into a box - `af_heart` - with nothing to say what it
sounded like. Each voice can now say one fixed sentence, made by the speech
service on this network the first time and kept after that.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.speech_service import VOICE_SAMPLE_TEXT
from tests.support import TestApp
from tests.test_speech_barge_in import SlowResponse


def _local_speech(running, **preferences) -> None:
    running.create_and_login()
    running.client.put(
        "/api/v1/settings",
        json={
            "tts_provider": preferences.pop("provider", "local"),
            "tts_format": "wav",
            "preferences": {"tts_local_base_url": "http://127.0.0.1:8880", **preferences},
        },
    )


class VoiceSampleTests(unittest.TestCase):
    def test_a_voice_says_the_sample_once_and_it_is_kept(self):
        asked = []

        def kokoro(request, timeout=None):
            asked.append(json.loads(request.data))
            return SlowResponse([b"sample-audio"], content_type="audio/mpeg")

        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _local_speech(running)
            with mock.patch("app.speech_clients.urllib.request.urlopen", side_effect=kokoro):
                first = running.client.get("/api/v1/speech/samples", params={"voice": "bf_emma"})
                again = running.client.get("/api/v1/speech/samples", params={"voice": "bf_emma"})

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["content-type"], "audio/mpeg")
        self.assertEqual(first.content, b"sample-audio")
        self.assertEqual(again.content, b"sample-audio")
        # Made once; the second time it is a recording.
        self.assertEqual(len(asked), 1)
        self.assertEqual((asked[0]["voice"], asked[0]["input"]), ("bf_emma", VOICE_SAMPLE_TEXT))
        # Mp3 whatever replies are stored as: it is small, and every browser plays it.
        self.assertEqual(asked[0]["response_format"], "mp3")

    def test_a_different_speed_is_a_different_sample(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _local_speech(running)
            with mock.patch(
                "app.speech_clients.urllib.request.urlopen",
                side_effect=lambda *_a, **_k: SlowResponse([b"audio"], content_type="audio/mpeg"),
            ) as kokoro:
                running.client.get("/api/v1/speech/samples", params={"voice": "af_heart", "speed": "1"})
                running.client.get("/api/v1/speech/samples", params={"voice": "af_heart", "speed": "1.3"})

        self.assertEqual(kokoro.call_count, 2)

    def test_only_a_voice_name_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _local_speech(running)
            with mock.patch("app.speech_clients.urllib.request.urlopen") as kokoro:
                for voice in ("../../settings", "af heart", ""):
                    response = running.client.get("/api/v1/speech/samples", params={"voice": voice})
                    self.assertEqual(response.status_code, 400, voice)
            kokoro.assert_not_called()

    def test_a_cloud_voice_is_not_sampled(self):
        # Browsing voices should not send anything off the machine.
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            _local_speech(running, provider="openai")
            with mock.patch("app.speech_clients.urllib.request.urlopen") as network:
                response = running.client.get("/api/v1/speech/samples", params={"voice": "marin"})
            self.assertEqual(response.status_code, 400)
            network.assert_not_called()

    def test_the_kept_samples_are_not_part_of_a_backup(self):
        # A cache, remade on demand: copying it into every snapshot is waste.
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            backed_up = {path for _name, path in running.services.operations.backups.media_dirs}
            self.assertNotIn(running.config.voice_sample_dir, backed_up)
            self.assertNotIn(running.config.voice_sample_dir.parent, backed_up)


if __name__ == "__main__":
    unittest.main()
