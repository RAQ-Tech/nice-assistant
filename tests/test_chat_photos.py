"""Photos sent into a conversation, for a persona whose model can see. ADR 0046."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from app.ollama_provider import _can_see
from app.provider_contracts import ModelContextProfile
from tests.support import FakeChatProvider, TestApp


class SeeingProvider(FakeChatProvider):
    def model_context(self, model):
        return ModelContextProfile("ollama", model, 8192, "fake", vision=True)


def _photo(width=2400, height=1200, description="taken at a private address") -> str:
    image = Image.new("RGB", (width, height), (200, 120, 40))
    exif = Image.Exif()
    exif[0x010E] = description  # ImageDescription
    exif[0x0112] = 6  # Orientation: shot sideways
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _send(running: TestApp, chat_id: str, text: str, photo: str | None = None):
    body = {"text": text, "memory_mode": "off", **({"photo": photo} if photo else {})}
    return running.client.post(f"/api/v1/chats/{chat_id}/turns", json=body)


class SendingAPhotoTests(unittest.TestCase):
    def test_the_model_sees_the_photo_upright_small_and_without_its_metadata(self):
        provider = SeeingProvider(["What a lovely colour."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            started = _send(running, chat["id"], "Look at this!", _photo())
            self.assertEqual(started.status_code, 202, started.text)
            self.assertEqual(running.wait_job(started.json()["job"]["id"])["status"], "completed")

            sent = provider.requests[-1].messages[-1]
            self.assertEqual(sent["content"], "Look at this!")
            self.assertEqual(len(sent["images"]), 1)
            with Image.open(BytesIO(base64.b64decode(sent["images"][0]))) as seen:
                self.assertEqual(seen.format, "JPEG")
                # Turned upright from the sideways shot, then shrunk.
                self.assertGreater(seen.height, seen.width)
                self.assertLessEqual(max(seen.size), 1568)
                # Where and when it was taken stays on the phone.
                self.assertEqual(len(seen.getexif()), 0)

    def test_a_photo_with_nothing_said_is_still_a_message(self):
        provider = SeeingProvider(["Oh, the sea!"])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            started = _send(running, chat["id"], "", _photo())
            self.assertEqual(started.status_code, 202, started.text)

    def test_a_model_that_cannot_see_refuses_before_anything_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            response = _send(running, chat["id"], "Look!", _photo())
            # A reply about a photo it never saw would be worse than no reply.
            self.assertEqual(response.status_code, 409)
            self.assertIn("can't see photos", response.text)
            self.assertEqual(running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"], [])

    def test_the_photo_is_shown_back_only_to_whoever_sent_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=SeeingProvider()) as running:
            running.create_and_login("owner")
            chat = running.client.post("/api/v1/chats", json={}).json()
            running.wait_job(_send(running, chat["id"], "Mine.", _photo()).json()["job"]["id"])
            photos = running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"][0]["photos"]
            self.assertEqual(len(photos), 1)
            served = running.client.get(photos[0]["url"])
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.headers["content-type"], "image/jpeg")

            running.client.delete("/api/v1/session")
            running.create_and_login("guest")
            self.assertEqual(running.client.get(photos[0]["url"]).status_code, 404)

    def test_a_follow_up_still_sees_the_photo_and_a_later_turn_does_not(self):
        provider = SeeingProvider(["Mm."])
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=provider) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            running.wait_job(_send(running, chat["id"], "My dog.", _photo()).json()["job"]["id"])
            running.wait_job(_send(running, chat["id"], "What breed is she?").json()["job"]["id"])
            follow_up = provider.requests[-1].messages
            self.assertEqual(sum(len(message.get("images") or []) for message in follow_up), 1)

            for text in ("Anyway.", "Other things.", "And more."):
                running.wait_job(_send(running, chat["id"], text).json()["job"]["id"])
            later = provider.requests[-1].messages
            self.assertEqual(sum(len(message.get("images") or []) for message in later), 0)

    def test_answering_again_keeps_the_photo_and_deleting_takes_it(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=SeeingProvider()) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            first = _send(running, chat["id"], "Look.", _photo()).json()
            running.wait_job(first["job"]["id"])
            again = running.client.post(
                f"/api/v1/chats/{chat['id']}/messages/{first['turn']['user_message_id']}/again",
                json={"text": "Look at this one."},
            ).json()
            running.wait_job(again["job"]["id"])
            said = running.client.get(f"/api/v1/chats/{chat['id']}").json()["messages"][0]
            self.assertEqual(said["text"], "Look at this one.")
            self.assertEqual(len(said["photos"]), 1)

            running.client.delete(f"/api/v1/chats/{chat['id']}/messages/{said['id']}")
            self.assertEqual(running.client.get(said["photos"][0]["url"]).status_code, 404)

    def test_anything_but_a_picture_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=SeeingProvider()) as running:
            running.create_and_login()
            chat = running.client.post("/api/v1/chats", json={}).json()
            fake = "data:image/png;base64," + base64.b64encode(b"not really a picture").decode("ascii")
            self.assertEqual(_send(running, chat["id"], "Look.", fake).status_code, 400)
            self.assertEqual(_send(running, chat["id"], "Look.", "https://example.test/a.png").status_code, 400)


class WhichModelsCanSeeTests(unittest.TestCase):
    def test_the_model_list_says_which_can_see(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp), chat_provider=SeeingProvider()) as running:
            running.create_and_login()
            listed = running.client.get("/api/v1/models").json()
            self.assertEqual(listed["vision_models"], listed["models"])

    def test_ollama_says_so_now_and_said_so_before(self):
        self.assertTrue(_can_see({"capabilities": ["completion", "vision"]}))
        self.assertFalse(_can_see({"capabilities": ["completion", "tools"]}))
        # Before `capabilities`, a vision tower showed up in the model info.
        self.assertTrue(_can_see({"model_info": {"gemma3.vision.block_count": 27}}))
        self.assertTrue(_can_see({"model_info": {"clip.vision.image_size": 336}}))
        self.assertFalse(_can_see({"model_info": {"llama.context_length": 8192}}))


if __name__ == "__main__":
    unittest.main()
