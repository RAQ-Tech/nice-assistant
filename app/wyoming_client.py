"""Speaking Wyoming, the protocol Home Assistant's voice services use.

Local transcription first spoke the OpenAI HTTP shape, on the reasoning that
somebody wanting it would deploy a server for it. Often they already have one and
it is not that: anybody running Home Assistant voice is running a Wyoming
speech-to-text service already, and asking them to install a second Whisper
beside the working one is asking them to spend a gigabyte to gain nothing.

Wyoming is a line of JSON over a TCP socket, optionally followed by a block of
JSON and a block of binary. That is the whole protocol, which is why this needs
no dependency to speak it.
"""

from __future__ import annotations

import json
import socket
import wave

# One event's JSON. Real ones are a few hundred bytes; this only exists so a
# confused or hostile peer cannot make the process allocate without bound.
MAX_EVENT_BYTES = 1024 * 1024
# Audio is sent in pieces because the server begins work on what it has rather
# than waiting for the last byte.
CHUNK_BYTES = 8192
DEFAULT_WYOMING_PORT = 10300


class WyomingUnavailable(Exception):
    """The service could not be reached or did not answer as expected."""


def parse_address(raw, default_port: int = DEFAULT_WYOMING_PORT) -> tuple[str, int]:
    """Split "host:port" into its parts, tolerating a scheme somebody pasted."""

    text = str(raw or "").strip()
    for scheme in ("tcp://", "http://", "https://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme) :]
    text = text.split("/", 1)[0].strip()
    if not text:
        raise WyomingUnavailable("No transcription service address is configured.")
    host, separator, port = text.rpartition(":")
    if not separator:
        return text, default_port
    try:
        number = int(port)
    except ValueError as exc:
        raise WyomingUnavailable("That address does not end in a port number.") from exc
    if not 1 <= number <= 65535:
        raise WyomingUnavailable("That address does not end in a port number.")
    return host or text, number


def _send(sock: socket.socket, kind: str, data=None, payload: bytes = b"") -> None:
    header = {"type": kind}
    if data is not None:
        header["data"] = data
    if payload:
        header["payload_length"] = len(payload)
    sock.sendall((json.dumps(header) + "\n").encode() + payload)


def _read_event(sock: socket.socket) -> tuple[str, dict]:
    """One event, header and any out-of-band data block, as a type and a dict."""

    buffer = b""
    while b"\n" not in buffer:
        piece = sock.recv(4096)
        if not piece:
            raise WyomingUnavailable("The transcription service closed the connection.")
        buffer += piece
        if len(buffer) > MAX_EVENT_BYTES:
            raise WyomingUnavailable("The transcription service sent an oversized event.")
    line, rest = buffer.split(b"\n", 1)
    try:
        header = json.loads(line)
    except ValueError as exc:
        raise WyomingUnavailable("The transcription service sent something that is not Wyoming.") from exc
    data = header.get("data") if isinstance(header.get("data"), dict) else {}
    length = int(header.get("data_length") or 0)
    if length > MAX_EVENT_BYTES:
        raise WyomingUnavailable("The transcription service sent an oversized event.")
    while len(rest) < length:
        piece = sock.recv(4096)
        if not piece:
            raise WyomingUnavailable("The transcription service closed the connection.")
        rest += piece
    if length:
        try:
            data = json.loads(rest[:length])
        except ValueError as exc:
            raise WyomingUnavailable("The transcription service sent malformed event data.") from exc
    return str(header.get("type") or ""), data if isinstance(data, dict) else {}


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise WyomingUnavailable(f"The transcription service could not be reached ({exc.__class__.__name__}).") from exc
    sock.settimeout(timeout)
    return sock


def wyoming_describe(address, timeout: float = 10.0) -> dict:
    """What the service is and which model it has loaded.

    Worth more than a reachability check: the answer names the model, and which
    Whisper is loaded is the difference between a transcript that arrives before
    you have stopped thinking and one that arrives well after.
    """

    host, port = parse_address(address)
    sock = _connect(host, port, timeout)
    try:
        _send(sock, "describe")
        kind, data = _read_event(sock)
    finally:
        sock.close()
    if kind != "info":
        raise WyomingUnavailable("That service did not describe itself when asked.")
    programs = data.get("asr") if isinstance(data.get("asr"), list) else []
    if not programs:
        raise WyomingUnavailable("That Wyoming service does not do speech to text.")
    program = programs[0] if isinstance(programs[0], dict) else {}
    models = [str(model.get("name")) for model in (program.get("models") or []) if isinstance(model, dict)]
    return {"name": str(program.get("name") or "unknown"), "models": models}


def wyoming_transcribe(address, wav_path: str, language: str = "auto", timeout: float = 120.0) -> dict:
    """Send a recording and read back what was said.

    The audio goes at whatever rate it was recorded at rather than being
    resampled here. The server resamples anyway, it does it with a real audio
    library, and doing it twice would only lose something.
    """

    host, port = parse_address(address)
    try:
        with wave.open(str(wav_path), "rb") as handle:
            audio = {
                "rate": handle.getframerate(),
                "width": handle.getsampwidth(),
                "channels": handle.getnchannels(),
            }
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error) as exc:
        raise WyomingUnavailable("The recording could not be read for transcription.") from exc
    if not frames:
        return {"text": "", "language": None}

    sock = _connect(host, port, timeout)
    try:
        # "auto" is this product's word for saying nothing, not a language code.
        _send(sock, "transcribe", {"language": language} if language and language != "auto" else {})
        _send(sock, "audio-start", {**audio, "timestamp": 0})
        for offset in range(0, len(frames), CHUNK_BYTES):
            _send(sock, "audio-chunk", {**audio, "timestamp": 0}, frames[offset : offset + CHUNK_BYTES])
        _send(sock, "audio-stop", {"timestamp": 0})
        while True:
            kind, data = _read_event(sock)
            if kind == "transcript":
                return {"text": str(data.get("text") or "").strip(), "language": data.get("language")}
            if kind == "error":
                raise WyomingUnavailable(str(data.get("text") or "The transcription service reported an error."))
    except socket.timeout as exc:
        raise WyomingUnavailable("The transcription service did not answer in time.") from exc
    finally:
        sock.close()
