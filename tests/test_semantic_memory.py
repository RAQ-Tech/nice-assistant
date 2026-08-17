"""A memory can be found by a question that shares none of its words.

Retrieval was keyword search plus recency, so "what do I drive" never found
"owns a 2019 Tacoma". A small local model gives each memory a vector, the
question gets one too, and the two are compared by direction.

Three things this must not do: put a weak match in front of an exact one, cost
anything when no embedding model is installed, or stop working because one
stored vector came from a different model.
"""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.embedding import (
    MAX_CANDIDATES,
    EmbeddingUnavailable,
    normalize,
    ollama_embed,
    pack,
    rank,
    similarity,
    unpack,
)
from tests.support import TestApp


class VectorTests(unittest.TestCase):
    def test_a_stored_vector_survives_the_round_trip(self):
        vector = normalize([3.0, 4.0])
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0, places=5)

        restored = unpack(pack(vector))
        for original, value in zip(vector, restored, strict=True):
            self.assertAlmostEqual(original, value, places=5)

    def test_direction_is_what_is_compared_not_length(self):
        # The same meaning said at different lengths is the same direction.
        self.assertAlmostEqual(similarity(normalize([1.0, 1.0]), normalize([5.0, 5.0])), 1.0, places=5)
        self.assertAlmostEqual(similarity(normalize([1.0, 0.0]), normalize([0.0, 1.0])), 0.0, places=5)

    def test_a_vector_from_another_model_scores_zero_rather_than_raising(self):
        # One stale row must not break a whole retrieval.
        self.assertEqual(similarity([1.0, 0.0], [1.0, 0.0, 0.0]), 0.0)
        self.assertEqual(similarity([], [1.0]), 0.0)

    def test_an_empty_or_zero_vector_is_refused(self):
        for values in ([], [0.0, 0.0]):
            with self.assertRaises(EmbeddingUnavailable):
                normalize(values)

    def test_a_weak_match_is_dropped_rather_than_ranked_last(self):
        query = normalize([1.0, 0.0])
        close = normalize([0.95, 0.05])
        unrelated = normalize([0.0, 1.0])

        ranked = rank(query, [("far", unrelated), ("near", close)])

        # A weak match in a context window is worse than no match: the model
        # reads whatever is there as relevant.
        self.assertEqual([identifier for identifier, _score in ranked], ["near"])

    def test_no_question_vector_means_no_semantic_opinion(self):
        self.assertEqual(rank(None, [("a", normalize([1.0, 0.0]))]), [])


class EmbeddingClientTests(unittest.TestCase):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            import json

            return json.dumps(self.payload).encode()

    def test_the_model_is_asked_locally_and_the_answer_is_normalised(self):
        captured = {}

        def fake_urlopen(request, timeout=None):
            import json

            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode())
            return self.Response({"embedding": [3.0, 4.0]})

        with mock.patch("app.embedding.urllib.request.urlopen", side_effect=fake_urlopen):
            vector = ollama_embed("http://127.0.0.1:11434", "nomic-embed-text", "a red car")

        self.assertTrue(captured["url"].endswith("/api/embeddings"))
        self.assertEqual(captured["payload"]["model"], "nomic-embed-text")
        # Normalised on the way in, so comparison later is a dot product and
        # nothing else.
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0, places=5)

    def test_an_unreachable_model_is_reported_rather_than_raised_raw(self):
        with mock.patch("app.embedding.urllib.request.urlopen", side_effect=OSError("refused")):
            with self.assertRaises(EmbeddingUnavailable):
                ollama_embed("http://127.0.0.1:11434", "nomic-embed-text", "hello")

    def test_a_response_without_a_vector_is_refused(self):
        with mock.patch("app.embedding.urllib.request.urlopen", return_value=self.Response({"error": "no model"})):
            with self.assertRaises(EmbeddingUnavailable):
                ollama_embed("http://127.0.0.1:11434", "nomic-embed-text", "hello")


class SemanticRecallTests(unittest.TestCase):
    """The whole path, with a stand-in for the model.

    The fake gives "car" and "vehicle" the same direction and everything else a
    different one, which is exactly the case keyword search cannot serve.
    """

    def _vector_for(self, text: str):
        lowered = str(text).lower()
        if "car" in lowered or "drive" in lowered or "tacoma" in lowered:
            return normalize([1.0, 0.0, 0.0])
        if "cat" in lowered:
            return normalize([0.0, 1.0, 0.0])
        return normalize([0.0, 0.0, 1.0])

    def _running(self, tmp):
        running = TestApp(Path(tmp))
        return running

    def _memory(self, running, content: str):
        created = running.client.post("/api/v1/memories", json={"content": content, "scope": "global"})
        assert created.status_code == 200, created.text
        return created.json()

    def test_a_memory_is_found_by_a_question_that_shares_no_words(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = "fake-embed"
            memories.embedding_base_url = "http://127.0.0.1:11434"
            self._memory(running, "Owns a 2019 Tacoma")
            self._memory(running, "Has a cat called Biscuit")

            with mock.patch(
                "app.memory_service.ollama_embed", side_effect=lambda _u, _m, text, **_kw: self._vector_for(text)
            ):
                report = memories.embed_pending()
                self.assertEqual(report["embedded"], 2)
                question = memories.question_vector("what do I drive")

            with running.services.memory._uow() as uow:
                found = uow.repo.relevant_memories(
                    user_id,
                    workspace_id=None,
                    persona_id=None,
                    chat_id="none",
                    search_query=None,
                    query_vector=question,
                )

            # No shared word between "what do I drive" and "Owns a 2019 Tacoma".
            self.assertEqual(found[0].content, "Owns a 2019 Tacoma")

    def test_nothing_changes_when_no_embedding_model_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = ""
            self._memory(running, "Owns a 2019 Tacoma")

            self.assertFalse(memories.semantic_recall_configured)
            self.assertIsNone(memories.question_vector("what do I drive"))
            self.assertEqual(memories.embed_pending()["embedded"], 0)

            with running.services.memory._uow() as uow:
                found = uow.repo.relevant_memories(
                    user_id, workspace_id=None, persona_id=None, chat_id="none", search_query=None
                )
            # Retrieval is exactly what it was before any of this existed.
            self.assertEqual([row.content for row in found], ["Owns a 2019 Tacoma"])

    def test_an_unreachable_model_leaves_the_memory_usable(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = "fake-embed"
            memories.embedding_base_url = "http://127.0.0.1:11434"
            self._memory(running, "Owns a 2019 Tacoma")

            with mock.patch("app.memory_service.ollama_embed", side_effect=EmbeddingUnavailable("model not pulled")):
                report = memories.embed_pending()
                self.assertIsNone(memories.question_vector("what do I drive"))

            self.assertEqual(report["embedded"], 0)
            self.assertIn("not pulled", report["reason"])
            with running.services.memory._uow() as uow:
                found = uow.repo.relevant_memories(
                    user_id, workspace_id=None, persona_id=None, chat_id="none", search_query=None
                )
            # A model that is down must not cost somebody their memories.
            self.assertEqual(len(found), 1)

    def test_an_exact_keyword_match_still_wins(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            user_id = running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = "fake-embed"
            memories.embedding_base_url = "http://127.0.0.1:11434"
            self._memory(running, "Owns a 2019 Tacoma")
            self._memory(running, "Biscuit the cat sleeps on the car")

            with mock.patch(
                "app.memory_service.ollama_embed", side_effect=lambda _u, _m, text, **_kw: self._vector_for(text)
            ):
                memories.embed_pending()
                question = memories.question_vector("tell me about Biscuit")

            with running.services.memory._uow() as uow:
                found = uow.repo.relevant_memories(
                    user_id,
                    workspace_id=None,
                    persona_id=None,
                    chat_id="none",
                    search_query='"biscuit"',
                    query_vector=question,
                )

            # Somebody who names a thing means that thing.
            self.assertEqual(found[0].content, "Biscuit the cat sleeps on the car")

    def test_the_reply_path_never_goes_looking_for_a_model(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = "fake-embed"
            memories.embedding_base_url = "http://127.0.0.1:11434"
            self._memory(running, "Owns a 2019 Tacoma")

            asked = []
            with mock.patch("app.memory_service.ollama_embed", side_effect=lambda *a, **k: asked.append(a)):
                # Nothing has proven the model is there, so a turn must not
                # spend a connection - or a timeout - finding out.
                self.assertIsNone(memories.question_vector("what do I drive"))

            self.assertEqual(asked, [])

    def test_a_model_that_goes_away_stops_being_asked(self):
        with tempfile.TemporaryDirectory() as tmp, TestApp(Path(tmp)) as running:
            running.create_and_login()
            memories = running.services.memory
            memories.embedding_model = "fake-embed"
            memories.embedding_base_url = "http://127.0.0.1:11434"
            self._memory(running, "Owns a 2019 Tacoma")

            with mock.patch(
                "app.memory_service.ollama_embed", side_effect=lambda _u, _m, text, **_k: self._vector_for(text)
            ):
                memories.embed_pending()
            with mock.patch("app.memory_service.ollama_embed", side_effect=EmbeddingUnavailable("gone")):
                self.assertIsNone(memories.question_vector("what do I drive"))

            calls = []
            with mock.patch("app.memory_service.ollama_embed", side_effect=lambda *a, **k: calls.append(a)):
                # One failure is enough; every later turn is free again until a
                # background pass finds the model.
                self.assertIsNone(memories.question_vector("what do I drive"))
            self.assertEqual(calls, [])

    def test_keeping_vectors_current_does_not_need_the_picture_scheduler(self):
        from app.scene_production import SceneProductionRunner

        class Memories:
            def __init__(self):
                self.passes = 0

            def embed_pending(self):
                self.passes += 1
                return {"embedded": 0, "pending": 0, "reason": ""}

        memories = Memories()
        runner = SceneProductionRunner(None, __import__("logging").getLogger("test"), enabled=False, memories=memories)

        self.assertEqual(runner.run_once(), [])
        # Recall must not quietly depend on a picture setting being on.
        self.assertEqual(memories.passes, 1)

    def test_only_a_handful_of_memories_are_promoted_by_meaning(self):
        from app.embedding import MAX_SEMANTIC_MATCHES, rank

        query = normalize([1.0, 0.0])
        # Twenty memories all comfortably above the floor.
        candidates = [(f"m{index}", normalize([1.0, index * 0.001])) for index in range(20)]

        ranked = rank(query, candidates)

        # Promoting twenty guesses ahead of what is merely recent is the noise
        # problem this feature is supposed to avoid, not cause.
        self.assertEqual(len(ranked), MAX_SEMANTIC_MATCHES)

    def test_the_floor_admits_what_a_real_model_scores_for_a_right_answer(self):
        from app.embedding import SIMILARITY_FLOOR

        # Measured against nomic-embed-text on twelve ordinary memories: right
        # answers ran 0.42 to 0.64, wrong ones had a median of 0.36. A floor
        # above the weakest right answer makes the feature look broken while
        # every part of it works.
        self.assertLessEqual(SIMILARITY_FLOOR, 0.42)
        self.assertGreater(SIMILARITY_FLOOR, 0.36)

    def test_the_work_is_bounded_however_much_is_remembered(self):
        # The ceiling on comparisons is what keeps this a fixed cost rather than
        # one that grows with how much the assistant knows.
        self.assertLessEqual(MAX_CANDIDATES, 1000)


if __name__ == "__main__":
    unittest.main()
