from __future__ import annotations

import secrets
import shutil
import subprocess
import time

from app.provider_contracts import ProviderError
from app.providers import user_safe_provider_error
from app.persona_voice import parse as parse_voice_preferences, preference as voice_preference
from app.wyoming_client import (
    WyomingUnavailable,
    parse_address as parse_wyoming_address,
    wyoming_transcribe,
)
from app.repositories import UnitOfWork
from app.service_errors import NotFoundError, RequestError
from app.speech_sessions import SpeechSessionRegistry
from app.speech_clients import (
    STREAMABLE_FORMATS,
    SpeechCancelled,
    DEFAULT_STT_MODEL,
    kokoro_list_voices,
    kokoro_speech,
    kokoro_speech_stream,
    local_stt,
    normalized_stt_base_url,
    openai_speech,
    openai_speech_stream,
    openai_stt,
)
from app.storage import write_artifact_atomic


FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}


class SpeechService:
    def __init__(self, session_factory, secret_store, config, logger, provider_url_policy=None, metrics=None):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.logger = logger
        self.provider_url_policy = provider_url_policy
        self.metrics = metrics
        self.sessions = SpeechSessionRegistry()

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def voices(self, user_id: str, base_url: str | None = None) -> list[str]:
        with self._uow() as uow:
            settings = uow.repo.settings(user_id)
        if not settings or settings["tts_provider"] != "local":
            return []
        url = str(base_url or settings["preferences"].get("tts_local_base_url") or "").strip()
        started = time.monotonic()
        outcome = "failed"
        try:
            if self.provider_url_policy:
                url = self.provider_url_policy.normalize(url or "http://127.0.0.1:8880", label="Local speech service")
            result = kokoro_list_voices(url)
            outcome = "completed"
            return result
        except Exception as exc:
            raise ProviderError(
                provider="local/kokoro",
                code="voices_unavailable",
                user_message=user_safe_provider_error("TTS", "local speech service", exc),
                retryable=True,
            ) from exc
        finally:
            if self.metrics:
                self.metrics.provider("local", "voices", outcome, int((time.monotonic() - started) * 1000))

    def synthesize(self, user_id: str, values: dict, cancelled=None) -> dict:
        """Speak this text, or stop the moment nobody is waiting for it.

        `cancelled` is polled while the provider response is read. Interrupting
        playback used to mute the browser and leave the provider generating
        audio nobody would hear, then write and rotate a file nobody asked for.
        A cancelled synthesis writes nothing at all.
        """

        plan = self._speech_plan(user_id, values)
        audio = self._provider_audio(plan, cancelled)
        return self._store_audio(user_id, plan, secrets.token_hex(8), audio)

    def stream_synthesis(self, user_id: str, values: dict, cancelled=None) -> tuple[str, str, object]:
        """Speak this text as it is produced, rather than after it is finished.

        Returns the id the finished audio will be stored under, its format, and
        an iterator of audio pieces. The id is known before the first byte so a
        caller can name the artifact in a response header and still let the
        browser start playing; the artifact itself is written only when the last
        piece has been produced, so an abandoned stream leaves nothing behind.
        """

        plan = self._speech_plan(user_id, values)
        if plan["format"] not in STREAMABLE_FORMATS:
            raise RequestError(
                f"'{plan['format']}' audio cannot be played before it is complete. "
                f"Choose one of: {', '.join(STREAMABLE_FORMATS)}.",
                400,
            )
        audio_id = secrets.token_hex(8)
        return audio_id, plan["format"], self._streamed_audio(user_id, plan, audio_id, cancelled)

    def begin_speech_session(self, user_id: str, values: dict) -> dict:
        """One reply about to be spoken a sentence at a time. See ADR 0042.

        The voice, model, speed and format are settled once, here, so every
        piece of the reply is spoken the same way. The id the recording will
        be stored under is known now, before a word has been said.
        """

        plan = self._speech_plan(user_id, values, text_required=False)
        if plan["format"] not in STREAMABLE_FORMATS:
            raise RequestError(
                f"'{plan['format']}' audio cannot be played before it is complete. "
                f"Choose one of: {', '.join(STREAMABLE_FORMATS)}.",
                400,
            )
        session = self.sessions.begin(user_id, plan)
        return {"session_id": session.id, "audio_id": session.id, "format": plan["format"]}

    def stream_session_piece(self, user_id: str, session_id: str, text, cancelled=None) -> tuple[str, object]:
        """Speak one finished sentence of the reply, as it is produced."""

        session = self.sessions.get(user_id, session_id)
        text = str(text or "").strip()
        if not text:
            raise RequestError("text required", 400)
        if len(text) > self.config.max_tts_text_chars:
            raise RequestError("TTS text too long", 413)
        if session.abandoned:
            raise RequestError("This reply's speech was stopped; nothing more of it is spoken.", 409)
        plan = {**session.plan, "text": text}
        return plan["format"], self._session_piece(session, plan, cancelled)

    def _session_piece(self, session, plan: dict, cancelled):
        started = time.monotonic()
        outcome = "failed"
        produced = []
        try:
            for piece in self._provider_stream(plan, cancelled):
                produced.append(piece)
                yield piece
            outcome = "completed"
        except SpeechCancelled:
            outcome = "cancelled"
            session.abandoned = True
            raise
        except GeneratorExit:
            # The browser stopped listening mid-piece. Nothing half-said
            # may become part of the recording, so the session is over.
            outcome = "cancelled"
            session.abandoned = True
            raise
        except RequestError:
            session.abandoned = True
            raise
        except Exception as exc:
            session.abandoned = True
            raise self._provider_failure(plan["provider"], exc) from exc
        finally:
            if self.metrics:
                self.metrics.provider(
                    plan["provider"], "speech_piece", outcome, int((time.monotonic() - started) * 1000)
                )
        # Only a piece that finished joins the recording.
        with session.lock:
            session.collected.append(b"".join(produced))

    def finish_speech_session(self, user_id: str, session_id: str) -> dict:
        """The reply is written and spoken: store it once, as one recording."""

        session = self.sessions.get(user_id, session_id)
        self.sessions.drop(session_id)
        if session.abandoned:
            raise RequestError("This reply's speech was stopped, so no recording was kept.", 409)
        with session.lock:
            audio = b"".join(session.collected)
        if not audio:
            raise RequestError("Nothing was spoken in this session.", 400)
        return self._store_audio(user_id, session.plan, session.id, audio)

    def abandon_speech_session(self, user_id: str, session_id: str) -> None:
        """The person stopped it. Quiet if it is already gone: a stop may race the reply's end."""

        try:
            session = self.sessions.get(user_id, session_id)
        except NotFoundError:
            return
        session.abandoned = True
        self.sessions.drop(session_id)

    def _streamed_audio(self, user_id: str, plan: dict, audio_id: str, cancelled):
        started = time.monotonic()
        outcome = "failed"
        collected = []
        try:
            for piece in self._provider_stream(plan, cancelled):
                collected.append(piece)
                yield piece
            outcome = "completed"
        except SpeechCancelled:
            outcome = "cancelled"
            raise
        except RequestError:
            raise
        except Exception as exc:
            raise self._provider_failure(plan["provider"], exc) from exc
        finally:
            if self.metrics:
                self.metrics.provider(
                    plan["provider"], "speech_stream", outcome, int((time.monotonic() - started) * 1000)
                )
        # Only a stream that finished becomes a file. One the browser walked
        # away from leaves nothing to store and nothing to rotate for.
        self._store_audio(user_id, plan, audio_id, b"".join(collected))

    def _provider_stream(self, plan: dict, cancelled):
        if plan["provider"] == "openai":
            if not plan["api_key"]:
                raise RequestError("OPENAI API key missing", 400)
            return openai_speech_stream(
                plan["text"],
                plan["voice"],
                plan["format"],
                plan["api_key"],
                plan["model"],
                plan["speed"],
                plan["instructions"],
                cancelled,
            )
        if plan["provider"] == "local":
            return kokoro_speech_stream(
                plan["text"],
                plan["voice"],
                plan["format"],
                plan["base_url"],
                plan["model"],
                plan["speed"],
                cancelled,
            )
        raise RequestError("Unknown TTS provider", 400)

    def _provider_failure(self, provider: str, exc: Exception) -> ProviderError:
        label = "OpenAI" if provider == "openai" else "local speech service"
        self.logger.warning("tts provider failed provider=%s error=%s", provider, exc.__class__.__name__)
        return ProviderError(
            provider=provider,
            code="synthesis_failed",
            user_message=user_safe_provider_error("TTS", label, exc),
            retryable=True,
        )

    def _speech_plan(self, user_id: str, values: dict, text_required: bool = True) -> dict:
        """Everything a synthesis needs, resolved once from settings and persona."""

        text = str(values.get("text") or "").strip()
        if not text and text_required:
            raise RequestError("text required", 400)
        if len(text) > self.config.max_tts_text_chars:
            raise RequestError("TTS text too long", 413)
        with self._uow() as uow:
            repo = uow.repo
            settings = repo.settings(user_id)
            persona_id = values.get("persona_id")
            persona = repo.persona(user_id, persona_id) if persona_id else None
            if persona_id and not persona:
                raise NotFoundError("persona not found")
            chat_id = values.get("chat_id")
            if chat_id and not repo.chat(user_id, chat_id):
                raise NotFoundError("chat not found")
            if not settings or settings["tts_provider"] == "disabled":
                raise RequestError("TTS disabled", 400)
            provider = settings["tts_provider"]
            preferences = settings["preferences"]
            # The persona's opinion is looked up by provider rather than read
            # from a column named after one, so a provider this deployment adds
            # later is honored without a schema change.
            wanted = parse_voice_preferences(getattr(persona, "voice_preferences_json", "{}")) if persona else {}
            voice = str(
                values.get("voice")
                or voice_preference(wanted, provider, "voice")
                or preferences.get(f"tts_voice_{provider}")
                or preferences.get("tts_voice")
                or ("af_heart" if provider == "local" else "marin")
            ).strip()
            model = str(
                values.get("model")
                or voice_preference(wanted, provider, "model")
                or preferences.get(f"tts_model_{provider}")
                or preferences.get("tts_model")
                or ("kokoro" if provider == "local" else "gpt-4o-mini-tts")
            ).strip()
            speed = str(
                values.get("speed")
                or voice_preference(wanted, provider, "speed")
                or preferences.get(f"tts_speed_{provider}")
                or preferences.get("tts_speed")
                or "1"
            )
            fmt = str(values.get("format") or settings["tts_format"] or "wav").strip().lower()
            if fmt not in FORMATS:
                raise RequestError("unsupported TTS format", 400)
            api_key = settings.get("openai_api_key")
            base_url = preferences.get("tts_local_base_url")
            if provider == "local" and self.provider_url_policy:
                base_url = self.provider_url_policy.normalize(
                    base_url or "http://127.0.0.1:8880",
                    label="Local speech service",
                )
            instructions = str(values.get("instructions") or preferences.get("tts_instructions_openai") or "").strip()
        return {
            "text": text,
            "provider": provider,
            "voice": voice,
            "model": model,
            "speed": speed,
            "format": fmt,
            "api_key": api_key,
            "base_url": base_url,
            "instructions": instructions,
            "persona_id": persona_id,
            "chat_id": chat_id,
        }

    def _provider_audio(self, plan: dict, cancelled) -> bytes:
        started = time.monotonic()
        outcome = "failed"
        try:
            if plan["provider"] == "openai":
                if not plan["api_key"]:
                    raise RequestError("OPENAI API key missing", 400)
                audio = openai_speech(
                    plan["text"],
                    plan["voice"],
                    plan["format"],
                    plan["api_key"],
                    plan["model"],
                    plan["speed"],
                    plan["instructions"],
                    cancelled,
                )
            elif plan["provider"] == "local":
                audio = kokoro_speech(
                    plan["text"],
                    plan["voice"],
                    plan["format"],
                    plan["base_url"],
                    plan["model"],
                    plan["speed"],
                    cancelled,
                )
            else:
                raise RequestError("Unknown TTS provider", 400)
            outcome = "completed"
            return audio
        except SpeechCancelled:
            outcome = "cancelled"
            raise
        except RequestError:
            raise
        except Exception as exc:
            raise self._provider_failure(plan["provider"], exc) from exc
        finally:
            if self.metrics:
                self.metrics.provider(plan["provider"], "speech", outcome, int((time.monotonic() - started) * 1000))

    def _store_audio(self, user_id: str, plan: dict, audio_id: str, audio: bytes) -> dict:
        target = self.config.audio_dir / f"{audio_id}.{plan['format']}"
        write_artifact_atomic(target, audio)
        with self._uow() as uow:
            uow.repo.add_audio(
                audio_id=audio_id,
                user_id=user_id,
                persona_id=plan["persona_id"],
                chat_id=plan["chat_id"],
                fmt=plan["format"],
                local_path=str(target),
            )
        self._rotate_audio()
        return {"audio_id": audio_id, "format": plan["format"]}

    def transcribe(self, user_id: str, filename: str, content: bytes) -> dict:
        with self._uow() as uow:
            settings = uow.repo.settings(user_id)
        provider = (settings or {}).get("stt_provider")
        if not settings or provider == "disabled":
            raise RequestError("STT disabled", 400)
        if provider not in ("openai", "local"):
            raise RequestError("No transcription provider is selected.", 400)
        api_key = settings.get("openai_api_key")
        if provider == "openai" and not api_key:
            raise RequestError("OPENAI API key missing", 400)
        extension = ".webm"
        lowered = str(filename or "").lower()
        if lowered.endswith((".mp4", ".m4a")):
            extension = ".mp4"
        elif lowered.endswith(".ogg"):
            extension = ".ogg"
        raw = self.config.data_dir / f"upload_{secrets.token_hex(6)}{extension}"
        wav = self.config.data_dir / f"upload_{secrets.token_hex(6)}.wav"
        try:
            raw.write_bytes(content)
            completed = subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw), str(wav)],
                check=False,
                capture_output=True,
            )
            if completed.returncode != 0 or not wav.exists():
                raise RequestError("Audio conversion failed. Please try again.", 500)
            result = self._transcribe_with(provider, settings, str(wav), api_key)
            if bool(settings["preferences"].get("stt_store_recordings", False)):
                stored = self.config.stt_recordings_dir / f"{user_id}_{secrets.token_hex(6)}{extension}"
                shutil.copy2(raw, stored)
            return {"text": result.get("text", ""), "language": result.get("language")}
        finally:
            raw.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

    def _transcription_target(self, settings: dict) -> tuple[str, str]:
        """The address and model of the Whisper service on this network."""

        preferences = settings["preferences"]
        base_url = normalized_stt_base_url(preferences.get("stt_local_base_url"))
        if self.provider_url_policy:
            base_url = self.provider_url_policy.normalize(base_url, label="Local transcription service")
        return base_url, str(preferences.get("stt_model_local") or DEFAULT_STT_MODEL).strip()

    def _wyoming_address(self, settings: dict) -> str:
        """A host and port, held to the same private-LAN policy as every URL.

        Wyoming is a socket rather than a URL, so the policy - which reads
        URLs - is given one built from the host. Skipping the check because the
        protocol is unusual would leave one local provider able to point at the
        internet while every other one cannot.
        """

        address = str(settings["preferences"].get("stt_wyoming_address") or "").strip()
        if not address:
            raise RequestError("No transcription service address is configured.", 400)
        try:
            host, port = parse_wyoming_address(address)
            if self.provider_url_policy:
                self.provider_url_policy.normalize(f"http://{host}:{port}", label="Local transcription service")
        except (WyomingUnavailable, ValueError) as exc:
            # A stored address that no longer passes - the allowlist narrowed,
            # or it predates this check - is a settings problem, and saying so
            # is what sends somebody to the page that can fix it.
            raise RequestError(str(exc), 400) from exc
        return f"{host}:{port}"

    def _transcribe_with(self, provider: str, settings: dict, wav: str, api_key) -> dict:
        """Hand the audio to whichever service is selected, cloud or local.

        The two paths differ only in where the request goes. Either failure is
        named after the provider that had it, so somebody reading a failed turn
        can tell whether their own Whisper service is down or somebody else's.
        """

        language = settings["preferences"].get("stt_language") or "auto"
        label = "local transcription service" if provider == "local" else "OpenAI"

        started = time.monotonic()
        outcome = "error"
        try:
            if provider == "local" and self._local_backend(settings) == "wyoming":
                result = wyoming_transcribe(self._wyoming_address(settings), wav, language)
            elif provider == "local":
                base_url, model = self._transcription_target(settings)
                result = local_stt(wav, base_url, model, language)
            else:
                result = openai_stt(wav, api_key, language)
            outcome = "ok"
            return result
        except RequestError:
            # Nothing was configured to fail. Reporting a provider failure would
            # send somebody looking at a service that was never contacted.
            raise
        except Exception as exc:
            self.logger.warning("stt provider failed provider=%s error=%s", provider, exc.__class__.__name__)
            raise ProviderError(
                provider=provider,
                code="transcription_failed",
                # Wyoming failures are already sentences somebody can act on -
                # wrong kind of service, no answer in time - and they are
                # authored here rather than echoed from a response body, so
                # there is nothing in them to leak. Every other failure goes
                # through the generic phrasing that assumes there might be.
                user_message=(
                    f"STT failed in the {label}. {exc}"
                    if isinstance(exc, WyomingUnavailable)
                    else user_safe_provider_error("STT", label, exc)
                ),
                retryable=True,
            ) from exc
        finally:
            if self.metrics:
                self.metrics.provider(provider, "stt", outcome, int((time.monotonic() - started) * 1000))

    @staticmethod
    def _local_backend(settings: dict) -> str:
        return str(settings["preferences"].get("stt_local_backend") or "openai_api").strip().lower()

    def _rotate_audio(self) -> None:
        files = sorted(
            (path for path in self.config.audio_dir.glob("*") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        archive = self.config.archive_dir / "audio"
        archive.mkdir(parents=True, exist_ok=True)
        while len(files) > self.config.audio_hot_limit:
            source = files.pop(0)
            target = archive / source.name
            try:
                shutil.move(str(source), target)
                with self._uow() as uow:
                    row = uow.repo.audio_by_path(str(source))
                    if row:
                        row.local_path = str(target)
            except Exception as exc:  # noqa: BLE001 - cache rotation cannot invalidate completed synthesis
                if target.exists() and not source.exists():
                    try:
                        shutil.move(str(target), source)
                    except OSError:
                        pass
                self.logger.warning("audio archive rotation failed error=%s", exc.__class__.__name__)
                break
