"""Turning text into a vector, and comparing vectors, without a numeric library.

Memory retrieval was keyword search plus recency, so a question that shared no
words with a memory never found it. This is the part that fixes that: a small
local model turns each memory into a vector when it is written, the question is
turned into one when it is asked, and the two are compared by direction.

There is no numpy here and none is wanted for this. Every vector is normalised
to unit length when it is stored, which turns the comparison into a plain dot
product - no square roots, no per-query normalisation - and the candidate set is
bounded, so the worst case is a fixed cost rather than one that grows with how
much the assistant remembers.

The model runs on the same machine as everything else. Conversation text does
not leave it; see `docs/task-models.md`.
"""

from __future__ import annotations

from array import array
import json
from operator import mul
import urllib.request


# Room for the usual small embedding models - 384 for all-minilm, 768 for
# nomic-embed-text, 1024 for mxbai-embed-large - and a refusal past that, since
# a vector this large is a misconfiguration rather than a better answer.
MAX_DIMENSIONS = 4096
# How many stored vectors one question is compared against. Comparison is a few
# microseconds each, so this is the ceiling on the whole added cost rather than
# a quality limit; beyond it, recency has already decided.
MAX_CANDIDATES = 400
# Measured, not guessed. Against twelve ordinary memories and nine questions on
# a real nomic-embed-text, right answers scored 0.42 to 0.64 and wrong ones had
# a median of 0.36. A floor of 0.55 - the value reasoned out before measuring -
# would have dropped four of the nine right answers, including the one this
# whole feature was described by. At 0.40 every right answer survives and about
# one wrong answer in six gets through, which the cap below then bounds.
SIMILARITY_FLOOR = 0.40
# How many memories meaning alone may promote ahead of recency. The floor keeps
# out the clearly unrelated; this keeps a handful of plausible-but-wrong ones
# from crowding out memories that are merely recent.
MAX_SEMANTIC_MATCHES = 6


class EmbeddingUnavailable(Exception):
    """No vector could be produced. Retrieval falls back rather than failing."""


def normalize(values) -> list[float]:
    """Scale to unit length so comparison is a dot product and nothing else."""

    vector = [float(value) for value in values]
    if not vector:
        raise EmbeddingUnavailable("The embedding model returned an empty vector.")
    if len(vector) > MAX_DIMENSIONS:
        raise EmbeddingUnavailable(f"The embedding model returned {len(vector)} dimensions.")
    length = sum(value * value for value in vector) ** 0.5
    if not length:
        raise EmbeddingUnavailable("The embedding model returned a zero vector.")
    return [value / length for value in vector]


def pack(vector) -> bytes:
    """Store as native float32. Half the bytes of a double, and ample here."""

    return array("f", vector).tobytes()


def unpack(raw: bytes | None) -> list[float]:
    if not raw:
        return []
    values = array("f")
    values.frombytes(raw)
    return list(values)


def similarity(left, right) -> float:
    """How closely two normalised vectors point the same way, from -1 to 1.

    Vectors of different lengths come from different models and are not
    comparable; that scores zero rather than raising, because one memory
    embedded by an older model must not break a whole retrieval.
    """

    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(map(mul, left, right)))


def rank(
    query_vector,
    candidates,
    *,
    floor: float = SIMILARITY_FLOOR,
    limit: int = MAX_SEMANTIC_MATCHES,
) -> list[tuple[str, float]]:
    """Score candidates against the question, best first.

    `candidates` is an iterable of `(identifier, vector)`. Anything below the
    floor is dropped rather than ranked last, and only the best few survive
    that: a short list where the top entry is usually right is worth more than
    a long one where it is buried.
    """

    if not query_vector:
        return []
    scored = []
    for identifier, vector in candidates:
        score = similarity(query_vector, vector)
        if score >= floor:
            scored.append((identifier, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[: max(1, int(limit))]


def ollama_embed(base_url: str, model: str, text: str, timeout: float = 30.0) -> list[float]:
    """Ask the local model for a vector, normalised and ready to store.

    The text is sent as written. nomic-embed-text documents `search_query:` and
    `search_document:` prefixes for exactly this use, and measuring them here
    made recall worse - narrower margins between right and wrong answers, and
    one question that had been answered correctly stopped being. Following the
    documentation would have been the reasonable thing to do and the wrong one.
    """

    payload = json.dumps({"model": model, "prompt": text}).encode()
    request = urllib.request.Request(
        f"{str(base_url or '').rstrip('/')}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 - every failure means the same thing here
        raise EmbeddingUnavailable(f"The embedding model could not be reached ({exc.__class__.__name__}).") from exc
    values = body.get("embedding") if isinstance(body, dict) else None
    if not isinstance(values, list):
        raise EmbeddingUnavailable("The embedding model did not return a vector.")
    return normalize(values)
