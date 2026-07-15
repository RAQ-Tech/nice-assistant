from __future__ import annotations

import base64
import json
from pathlib import Path
import secrets
import urllib.parse
import urllib.request


def normalize_tts_speed(speed) -> float:
    try:
        parsed = float(speed)
    except (TypeError, ValueError):
        return 1.0
    return min(4.0, max(0.25, parsed))


def openai_speech(text, voice, fmt, api_key, model="gpt-4o-mini-tts", speed="1", instructions=""):
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
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def normalized_kokoro_base_url(raw_url):
    return str(raw_url or "http://127.0.0.1:8880").strip().rstrip("/")


def kokoro_speech(text, voice, fmt, base_url, model="kokoro", speed="1"):
    base_url = normalized_kokoro_base_url(base_url)
    payload = json.dumps(
        {
            "model": model or "kokoro",
            "input": text,
            "voice": voice or "af_heart",
            "response_format": fmt,
            "speed": normalize_tts_speed(speed),
            "stream": False,
        }
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json", "x-raw-response": "true"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = response.read()
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
            return response.read()
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


def openai_stt(filepath, api_key, language="auto"):
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

    add("model", "whisper-1")
    if language and language != "auto":
        add("language", str(language))
    add("file", audio, filename="audio.wav", content_type="audio/wav")
    parts.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())
