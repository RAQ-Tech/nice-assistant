"""The Whisper somebody already runs, rather than a second one beside it.

Local transcription first spoke only the OpenAI HTTP shape. Anybody running Home
Assistant voice already has a Wyoming speech-to-text service, and telling them to
install a second Whisper to gain nothing is not a deployment step, it is a reason
not to bother.

A fake Wyoming server stands in for the real one here. The protocol is the thing
under test - the event order, the audio framing, the reply - because getting it
subtly wrong is how this fails against a real service while passing against a
mock that agrees with the mistake.
"""

from contextlib import closing
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest import mock
import wave

from app.wyoming_client import (
    WyomingUnavailable,
    parse_address,
    wyoming_describe,
    wyoming_transcribe,
)
from tests.support import TestApp


class FakeWyomingServer:
    """A Wyoming service that records what it was told, and answers.

    `reply` decides the ending: a transcript, or an error event.
    """

    def __init__(self, reply: str = "transcript", text: str = "the lighthouse"):
        self.reply = reply
        self.text = text
        self.events: list[tuple[str, dict]] = []
        self.audio = bytearray()
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(4)
        self.address = f"127.0.0.1:{self._socket.getsockname()[1]}"
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            try:
                self._handle(connection)
            except OSError:
                pass
            finally:
                connection.close()

    def _handle(self, connection: socket.socket) -> None:
        buffer = b""
        while True:
            while b"\n" not in buffer:
                piece = connection.recv(4096)
                if not piece:
                    return
                buffer += piece
            line, buffer = buffer.split(b"\n", 1)
            header = json.loads(line)
            payload_length = int(header.get("payload_length") or 0)
            while len(buffer) < payload_length:
                buffer += connection.recv(4096)
            payload, buffer = buffer[:payload_length], buffer[payload_length:]
            kind = str(header.get("type") or "")
            self.events.append((kind, header.get("data") or {}))
            if kind == "audio-chunk":
                self.audio.extend(payload)
            elif kind == "describe":
                self._send(
                    connection,
                    "info",
                    {"asr": [{"name": "faster-whisper", "models": [{"name": "large-v3"}]}]},
                )
                return
            elif kind == "audio-stop":
                if self.reply == "error":
                    self._send(connection, "error", {"text": "the model is not loaded"})
                else:
                    self._send(connection, "transcript", {"text": self.text})
                return

    @staticmethod
    def _send(connection: socket.socket, kind: str, data: dict) -> None:
        # Sent out of band behind data_length, which is what a real service does
        # for anything large and what this client therefore has to handle.
        body = json.dumps(data).encode()
        header = json.dumps({"type": kind, "version": "1.10.0", "data_length": len(body)}).encode()
        connection.sendall(header + b"\n" + body)

    def close(self) -> None:
        self._running = False
        self._socket.close()


def a_wav(folder: str, seconds: float = 0.2, rate: int = 24000) -> str:
    path = Path(folder) / "turn.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return str(path)


class AddressTests(unittest.TestCase):
    def test_a_bare_host_gets_the_protocol_default_port(self):
        self.assertEqual(parse_address("voicebox"), ("voicebox", 10300))

    def test_a_scheme_somebody_pasted_is_tolerated(self):
        # Every other address field in this product is a URL, so somebody will
        # type one here. Refusing it would be technically correct and annoying.
        self.assertEqual(parse_address("tcp://voicebox:10300/"), ("voicebox", 10300))
        self.assertEqual(parse_address("http://voicebox:10300"), ("voicebox", 10300))

    def test_nonsense_is_refused_rather_than_guessed_at(self):
        for bad in ("", "   ", "voicebox:notaport", "voicebox:0", "voicebox:99999"):
            with self.assertRaises(WyomingUnavailable):
                parse_address(bad)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeWyomingServer()
        self.addCleanup(self.server.close)

    def test_the_recording_arrives_between_a_start_and_a_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            result = wyoming_transcribe(self.server.address, a_wav(folder), "en")

        self.assertEqual(result["text"], "the lighthouse")
        order = [kind for kind, _ in self.server.events]
        # A chunk before audio-start, or a stop that never arrives, is how this
        # passes here and fails against something real.
        self.assertEqual(order[0], "transcribe")
        self.assertEqual(order[1], "audio-start")
        self.assertEqual(order[-1], "audio-stop")
        self.assertTrue(all(kind == "audio-chunk" for kind in order[2:-1]))

    def test_the_audio_is_described_by_what_it_actually_is(self):
        with tempfile.TemporaryDirectory() as folder:
            wyoming_transcribe(self.server.address, a_wav(folder, rate=24000), "en")

        start = next(data for kind, data in self.server.events if kind == "audio-start")
        # Declaring 16 kHz while sending 24 kHz makes a service transcribe
        # chipmunks. The rate travels with the audio it describes.
        self.assertEqual((start["rate"], start["width"], start["channels"]), (24000, 2, 1))
        self.assertEqual(len(self.server.audio), 24000 * 2 // 5)

    def test_automatic_language_asks_for_no_language(self):
        with tempfile.TemporaryDirectory() as folder:
            wyoming_transcribe(self.server.address, a_wav(folder), "auto")

        # "auto" is this product's word for saying nothing, not a language code.
        self.assertEqual(next(data for kind, data in self.server.events if kind == "transcribe"), {})

    def test_a_named_language_is_passed_through(self):
        with tempfile.TemporaryDirectory() as folder:
            wyoming_transcribe(self.server.address, a_wav(folder), "es")

        self.assertEqual(
            next(data for kind, data in self.server.events if kind == "transcribe"),
            {"language": "es"},
        )

    def test_the_service_naming_its_model_is_what_the_check_reports(self):
        described = wyoming_describe(self.server.address)

        # Which Whisper is loaded decides whether a spoken turn is usable, so
        # the check says which rather than only that something answered.
        self.assertEqual(described, {"name": "faster-whisper", "models": ["large-v3"]})

    def test_an_error_event_becomes_a_failure_that_says_why(self):
        self.server.reply = "error"
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(WyomingUnavailable) as caught:
                wyoming_transcribe(self.server.address, a_wav(folder), "en")

        self.assertIn("the model is not loaded", str(caught.exception))

    def test_nothing_listening_is_a_failure_rather_than_a_hang(self):
        # A port nothing ever listened on, rather than one whose server was
        # closed. Closing a listening socket does not wake a thread already
        # blocked in accept() on POSIX, so the fake server carried on answering
        # and this passed on Windows while failing every Linux build.
        with closing(socket.socket()) as spare:
            spare.bind(("127.0.0.1", 0))
            address = f"127.0.0.1:{spare.getsockname()[1]}"

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(WyomingUnavailable):
                wyoming_transcribe(address, a_wav(folder), "en", timeout=3.0)

    def test_silence_transcribes_to_nothing_without_a_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "empty.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
            result = wyoming_transcribe(self.server.address, str(path), "en")

        self.assertEqual(result["text"], "")
        self.assertEqual(self.server.events, [])


class ThroughTheProductTests(unittest.TestCase):
    """The wire, because the wire is where a settings key gets forgotten."""

    def test_a_spoken_turn_reaches_the_service_somebody_already_runs(self):
        server = FakeWyomingServer(text="what do I drive")
        self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            saved = running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {
                        "stt_local_backend": "wyoming",
                        "stt_wyoming_address": server.address,
                    },
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)

            response = _transcribe(running)

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["text"], "what do I drive")

    def test_a_missing_address_is_a_settings_problem_not_a_provider_failure(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={"stt_provider": "local", "preferences": {"stt_local_backend": "wyoming"}},
            )

            response = _transcribe(running)

            # Reporting a provider failure would send somebody looking at a
            # service that was never contacted.
            self.assertEqual(response.status_code, 400, response.text)

    def test_an_address_outside_the_private_lan_policy_never_gets_stored(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()

            refused = running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {
                        "stt_local_backend": "wyoming",
                        "stt_wyoming_address": "somebody-elses-box.example.com:10300",
                    },
                },
            )

            # A socket is not a URL, but skipping the check because the protocol
            # is unusual would leave one local provider able to reach the
            # internet while every other one cannot, under a label saying it
            # does not.
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertIn("private-LAN", refused.json()["error"]["message"])

    def test_a_stored_address_that_no_longer_passes_is_a_settings_problem(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            running.client.put(
                "/api/v1/settings",
                json={
                    "stt_provider": "local",
                    "preferences": {"stt_local_backend": "wyoming", "stt_wyoming_address": "127.0.0.1:10300"},
                },
            )
            # Whatever put it there - a narrowed allowlist, an account older
            # than the check - the runtime refuses it too rather than dialling.
            with mock.patch.object(
                running.services.speech.provider_url_policy,
                "normalize",
                side_effect=ValueError("host is outside the private-LAN provider policy"),
            ):
                response = _transcribe(running)

            self.assertEqual(response.status_code, 400, response.text)


def _transcribe(running):
    def fake_run(command, **_kwargs):
        # ffmpeg is a real dependency of this path and not what is under test.
        with wave.open(command[-1], "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(struct.pack("<h", 0) * 1600)
        return mock.Mock(returncode=0)

    with mock.patch("app.speech_service.subprocess.run", side_effect=fake_run):
        return running.client.post(
            "/api/v1/speech/transcriptions",
            files={"file": ("turn.webm", b"webm bytes", "audio/webm")},
        )


if __name__ == "__main__":
    unittest.main()
