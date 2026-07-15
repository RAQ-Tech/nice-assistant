from __future__ import annotations

import secrets
import shutil
import subprocess
import time

from app.provider_contracts import ProviderError
from app.providers import user_safe_provider_error
from app.repositories import UnitOfWork
from app.service_errors import NotFoundError, RequestError
from app.speech_clients import kokoro_list_voices, kokoro_speech, openai_speech, openai_stt
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

    def synthesize(self, user_id: str, values: dict) -> dict:
        text = str(values.get("text") or "").strip()
        if not text:
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
            voice = str(
                values.get("voice")
                or (getattr(persona, f"preferred_voice_{provider}", None) if persona else None)
                or preferences.get(f"tts_voice_{provider}")
                or preferences.get("tts_voice")
                or ("af_heart" if provider == "local" else "marin")
            ).strip()
            model = str(
                values.get("model")
                or (getattr(persona, f"preferred_tts_model_{provider}", None) if persona else None)
                or preferences.get(f"tts_model_{provider}")
                or preferences.get("tts_model")
                or ("kokoro" if provider == "local" else "gpt-4o-mini-tts")
            ).strip()
            speed = str(
                values.get("speed")
                or (getattr(persona, f"preferred_tts_speed_{provider}", None) if persona else None)
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
        started = time.monotonic()
        outcome = "failed"
        try:
            if provider == "openai":
                if not api_key:
                    raise RequestError("OPENAI API key missing", 400)
                audio = openai_speech(text, voice, fmt, api_key, model, speed, instructions)
            elif provider == "local":
                audio = kokoro_speech(text, voice, fmt, base_url, model, speed)
            else:
                raise RequestError("Unknown TTS provider", 400)
            outcome = "completed"
        except RequestError:
            raise
        except Exception as exc:
            label = "OpenAI" if provider == "openai" else "local speech service"
            self.logger.warning("tts provider failed provider=%s error=%s", provider, exc.__class__.__name__)
            raise ProviderError(
                provider=provider,
                code="synthesis_failed",
                user_message=user_safe_provider_error("TTS", label, exc),
                retryable=True,
            ) from exc
        finally:
            if self.metrics:
                self.metrics.provider(provider, "speech", outcome, int((time.monotonic() - started) * 1000))
        audio_id = secrets.token_hex(8)
        target = self.config.audio_dir / f"{audio_id}.{fmt}"
        write_artifact_atomic(target, audio)
        with self._uow() as uow:
            uow.repo.add_audio(
                audio_id=audio_id,
                user_id=user_id,
                persona_id=persona_id,
                chat_id=chat_id,
                fmt=fmt,
                local_path=str(target),
            )
        self._rotate_audio()
        return {"audio_id": audio_id, "format": fmt}

    def transcribe(self, user_id: str, filename: str, content: bytes) -> dict:
        with self._uow() as uow:
            settings = uow.repo.settings(user_id)
        if not settings or settings["stt_provider"] == "disabled":
            raise RequestError("STT disabled", 400)
        if settings["stt_provider"] != "openai":
            raise RequestError("Local STT is not implemented. Select OpenAI or disable STT.", 501)
        api_key = settings.get("openai_api_key")
        if not api_key:
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
            language = settings["preferences"].get("stt_language") or "auto"
            try:
                result = openai_stt(str(wav), api_key, language)
            except Exception as exc:
                raise ProviderError(
                    provider="openai",
                    code="transcription_failed",
                    user_message=user_safe_provider_error("STT", "OpenAI", exc),
                    retryable=True,
                ) from exc
            if bool(settings["preferences"].get("stt_store_recordings", False)):
                stored = self.config.stt_recordings_dir / f"{user_id}_{secrets.token_hex(6)}{extension}"
                shutil.copy2(raw, stored)
            return {"text": result.get("text", ""), "language": result.get("language")}
        finally:
            raw.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

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
