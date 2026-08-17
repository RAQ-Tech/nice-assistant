"""Which parts of a conversation stay on this machine, and which do not.

Nice Assistant runs local and cloud providers side by side on purpose. Somebody
who wants a fully self-contained assistant should be able to have one; somebody
who wants a better transcription than their hardware can manage should be able
to have that instead. Neither is the wrong answer.

What is wrong is not knowing which one you have. So the rule is not "local
only" - it is that every default is local or off, nothing silently escalates
from a local provider to a cloud one, and the product can say, in one place, in
plain words, where each part of a conversation currently goes.
"""

from __future__ import annotations


# Provider keys that send data to somebody else's computer. Names, not
# guesswork: a provider added later is off this list until somebody puts it on,
# and being absent means it is described as local, so the list is the thing to
# keep honest.
CLOUD_PROVIDERS = frozenset(
    {
        "openai",
        "openai-image",
        "openai-video",
    }
)

LOCAL = "local"
CLOUD = "cloud"
OFF = "off"


def locality(provider: str | None) -> str:
    """Where a provider sends what it is given."""

    name = str(provider or "").strip().lower()
    if not name or name == "disabled":
        return OFF
    return CLOUD if name in CLOUD_PROVIDERS else LOCAL


def is_cloud(provider: str | None) -> bool:
    return locality(provider) == CLOUD


def _entry(label: str, provider: str | None, detail: str) -> dict:
    return {"label": label, "provider": str(provider or "disabled"), "locality": locality(provider), "detail": detail}


def conversation_locality(settings: dict, task_provider: str | None, embedding_configured: bool) -> dict:
    """Where each part of a conversation goes, right now, for this account.

    Written for somebody deciding whether to say something, not for somebody
    debugging a provider. Every line names a thing that happens during a
    conversation rather than a subsystem.
    """

    preferences = (settings or {}).get("preferences") or {}
    image_provider = str(preferences.get("image_provider") or "disabled")
    # The image catalog calls its local backend "local"; the setting says which.
    parts = [
        _entry("What you type", "ollama", "The reply itself is written by the chat model."),
        _entry("What you say", (settings or {}).get("stt_provider"), "Recordings are transcribed."),
        _entry("What you hear", (settings or {}).get("tts_provider"), "Replies are read aloud."),
        _entry("Pictures", image_provider, "Images are generated."),
        _entry(
            "Background jobs",
            task_provider,
            "Chat titles, summaries, remembering facts, and planning pictures.",
        ),
        _entry(
            "Finding memories",
            "ollama" if embedding_configured else "disabled",
            "Matching a question to something remembered.",
        ),
    ]
    return {
        "parts": parts,
        "everything_local": all(part["locality"] != CLOUD for part in parts),
    }
