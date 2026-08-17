from __future__ import annotations

import base64
import json
from pathlib import Path
import secrets
import urllib.parse
import urllib.request


# Read size for a provider audio response. Small enough that an interruption is
# noticed promptly, large enough not to make a syscall per sentence.
AUDIO_CHUNK_BYTES = 64 * 1024
# A single reply's audio. Past this the provider is misbehaving, and reading it
# into memory is a denial of service against this process rather than a feature.
MAX_AUDIO_BYTES = 64 * 1024 * 1024


class SpeechCancelled(Exception):
    """Nobody is listening any more, so the rest of this work is waste."""


# Formats a browser can begin playing before the whole file has arrived. WAV
# carries its length in a header nothing can fill in halfway through, so a WAV
# reply cannot start early no matter how it is delivered.
STREAMABLE_FORMATS = ("mp3", "aac")


def _iter_cancellable(response, cancelled=None):
    """Yield a provider response in pieces, stopping the moment nobody wants it.

    Reading the whole body in one call means an interruption is only noticed
    after the provider has finished, which is the "muted the output but kept the
    work running" behaviour barge-in exists to remove. It also means the first
    byte of audio cannot reach the browser until the last one exists. Closing
    the connection is what tells the provider to stop generating.
    """

    # `read` waits for the full amount; `read1` returns what has arrived. That
    # difference is the whole of progressive delivery.
    read = getattr(response, "read1", None) or response.read
    total = 0
    while True:
        if cancelled and cancelled():
            raise SpeechCancelled()
        piece = read(AUDIO_CHUNK_BYTES)
        if not piece:
            return
        total += len(piece)
        if total > MAX_AUDIO_BYTES:
            raise ValueError("The speech provider returned more audio than this deployment will hold.")
        yield piece


def _read_cancellable(response, cancelled=None) -> bytes:
    return b"".join(_iter_cancellable(response, cancelled))


# Where a self-hosted Whisper service is expected unless one is configured.
# speaches - the maintained successor to faster-whisper-server - serves here.
DEFAULT_STT_BASE_URL = "http://127.0.0.1:8000"
# OpenAI's own transcription model name. Most self-hosted servers accept it as
# an alias for whatever they loaded; the ones that want a real identifier say
# so plainly, which is why this is a setting rather than a constant.
DEFAULT_STT_MODEL = "whisper-1"


def normalize_tts_speed(speed) -> float:
    try:
        parsed = float(speed)
    except (TypeError, ValueError):
        return 1.0
    return min(4.0, max(0.25, parsed))


def openai_speech(text, voice, fmt, api_key, model="gpt-4o-mini-tts", speed="1", instructions="", cancelled=None):
    request = _openai_speech_request(text, voice, fmt, api_key, model, speed, instructions)
    with urllib.request.urlopen(request, timeout=120) as response:
        return _read_cancellable(response, cancelled)


def _openai_speech_request(text, voice, fmt, api_key, model, speed, instructions):
    payload = json.dumps(
        {
            "model": model or "gpt-4o-mini-tts",
            "input": text,
            "voice": voice or "marin",
            "response_format": fmt,
            "speed": normalize_tts_speed(speed),
            **({"instructions": str(instructions).strip()} if str(instructions or "").strip() else {}),
        }
    ).encode()
    return urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )


def openai_speech_stream(
    text, voice, fmt, api_key, model="gpt-4o-mini-tts", speed="1", instructions="", cancelled=None
):
    """Yield OpenAI audio as it arrives rather than after it is complete."""

    request = _openai_speech_request(text, voice, fmt, api_key, model, speed, instructions)
    with urllib.request.urlopen(request, timeout=120) as response:
        yield from _iter_cancellable(response, cancelled)


def normalized_kokoro_base_url(raw_url):
    return str(raw_url or "http://127.0.0.1:8880").strip().rstrip("/")


def _kokoro_speech_request(base_url, text, voice, fmt, model, speed, stream):
    payload = json.dumps(
        {
            "model": model or "kokoro",
            "input": text,
            "voice": voice or "af_heart",
            "response_format": fmt,
            "speed": normalize_tts_speed(speed),
            "stream": bool(stream),
        }
    ).encode()
    return urllib.request.Request(
        f"{base_url}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "x-raw-response": "true"},
        method="POST",
    )


def kokoro_speech_stream(text, voice, fmt, base_url, model="kokoro", speed="1", cancelled=None):
    """Yield Kokoro audio as it is generated rather than after it is complete."""

    base_url = normalized_kokoro_base_url(base_url)
    request = _kokoro_speech_request(base_url, text, voice, fmt, model, speed, True)
    with urllib.request.urlopen(request, timeout=300) as response:
        content_type = (response.headers.get("Content-Type") or "").lower()
        if not content_type.startswith("audio/") and fmt != "pcm":
            # The completed-file path knows how to follow a download link. A
            # stream cannot, and pretending otherwise would hand the browser a
            # JSON body to play.
            raise ValueError(f"The speech provider answered with {content_type or 'no content type'}, not audio.")
        yield from _iter_cancellable(response, cancelled)


def kokoro_speech(text, voice, fmt, base_url, model="kokoro", speed="1", cancelled=None):
    base_url = normalized_kokoro_base_url(base_url)
    request = _kokoro_speech_request(base_url, text, voice, fmt, model, speed, False)
    with urllib.request.urlopen(request, timeout=300) as response:
        body = _read_cancellable(response, cancelled)
        content_type = (response.headers.get("Content-Type") or "").lower()
    if content_type.startswith("audio/") or fmt == "pcm":
        return body
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise ValueError(f"Unexpected Kokoro response ({content_type or 'unknown'}).") from exc
    download_url = str(parsed.get("download_url") or parsed.get("url") or "").strip()
    if download_url:
        request = urllib.request.Request(
            urllib.parse.urljoin(f"{base_url}/", download_url.lstrip("/")),
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            return _read_cancellable(response, cancelled)
    audio = parsed.get("audio_base64") or parsed.get("audio")
    if audio:
        return base64.b64decode(audio)
    raise ValueError("Kokoro response did not include audio bytes.")


def kokoro_list_voices(base_url):
    request = urllib.request.Request(f"{normalized_kokoro_base_url(base_url)}/v1/audio/voices", method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    voices = []
    if isinstance(payload, list):
        voices = [str(value).strip() for value in payload]
    elif isinstance(payload, dict):
        for key in ("voices", "data", "items"):
            if not isinstance(payload.get(key), list):
                continue
            voices = [
                str(value if isinstance(value, str) else (value.get("id") if isinstance(value, dict) else "")).strip()
                for value in payload[key]
            ]
            break
    return sorted({voice for voice in voices if voice})


def _transcription_body(filepath, model, language) -> tuple[bytes, str]:
    """The multipart body every OpenAI-shaped transcription endpoint takes."""

    boundary = "----NiceAssistantBoundary" + secrets.token_hex(8)
    audio = Path(filepath).read_bytes()
    parts = []

    def add(name, value, filename=None, content_type="text/plain"):
        parts.append(f"--{boundary}\r\n".encode())
        if filename:
            parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            parts.extend((value, b"\r\n"))
        else:
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

    add("model", str(model or "whisper-1"))
    if language and language != "auto":
        add("language", str(language))
    add("file", audio, filename="audio.wav", content_type="audio/wav")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


def openai_stt(filepath, api_key, language="auto"):
    body, boundary = _transcription_body(filepath, "whisper-1", language)
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def normalized_stt_base_url(raw_url):
    return str(raw_url or DEFAULT_STT_BASE_URL).strip().rstrip("/")


def local_stt(filepath, base_url, model=DEFAULT_STT_MODEL, language="auto"):
    """Transcribe against a Whisper service on this network, not OpenAI's.

    The request is the shape OpenAI documents, because that is what the
    self-hosted Whisper servers implement - speaches, whisper.cpp's own
    server, LocalAI. Speaking the shape rather than binding to one of them
    keeps this a configuration choice instead of a dependency, exactly as
    the local speech path already does for Kokoro.

    No API key is sent. A service on the private LAN that wanted one would
    be a different kind of thing than this is for.
    """

    body, boundary = _transcription_body(filepath, model, language)
    request = urllib.request.Request(
        f"{normalized_stt_base_url(base_url)}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(payload)
    except ValueError:
        # whisper.cpp answers text/plain unless asked otherwise. A transcript
        # is a transcript; refusing it over its content type would be pedantry.
        return {"text": payload.strip(), "language": None}
    if not isinstance(parsed, dict):
        return {"text": "", "language": None}
    return parsed
