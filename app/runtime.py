from __future__ import annotations

from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.database import build_engine, initialize_database
from app.observability import MetricsRegistry, RedactedJsonFormatter
from app.secret_store import SecretStore


SESSION_COOKIE = "nice_assistant_session"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    archive_dir: Path
    ollama_base_url: str = "http://127.0.0.1:11434"
    automatic1111_base_url: str = "http://127.0.0.1:7860"
    comfyui_base_url: str = "http://127.0.0.1:8188"
    session_ttl_seconds: int = 1800
    allow_public_signup: bool = False
    audio_hot_limit: int = 200
    backup_snapshot_limit: int = 10
    provider_timeout_seconds: float = 10.0
    generation_timeout_seconds: float = 120.0
    max_json_body_bytes: int = 1024 * 1024
    max_upload_body_bytes: int = 32 * 1024 * 1024
    max_tts_text_chars: int = 20_000
    interactive_workers: int = 1
    media_workers: int = 1
    default_context_window_tokens: int = 4096
    context_summary_trigger_ratio: float = 0.75
    context_max_compaction_passes: int = 2
    memory_candidate_limit: int = 5
    secure_cookies: bool = False
    trust_proxy_headers: bool = False
    allowed_origins: tuple[str, ...] = ()
    provider_allowed_hosts: tuple[str, ...] = ()
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    login_lockout_seconds: int = 900
    minimum_free_storage_bytes: int = 128 * 1024 * 1024
    audio_archive_retention_days: int = 30
    stt_recording_retention_days: int = 30
    log_archive_retention_days: int = 30
    daily_database_backup_limit: int = 14
    web_dir: Path = WEB_DIR

    @classmethod
    def from_env(cls) -> "AppConfig":
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        archive_dir = Path(os.getenv("ARCHIVE_DIR", "/archives"))
        max_json = max(1024, int(os.getenv("MAX_JSON_BODY_BYTES", str(1024 * 1024))))
        return cls(
            data_dir=data_dir,
            archive_dir=archive_dir,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            automatic1111_base_url=os.getenv("AUTOMATIC1111_BASE_URL", "http://127.0.0.1:7860").rstrip("/"),
            comfyui_base_url=os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/"),
            session_ttl_seconds=int(os.getenv("SESSION_TTL_SECONDS", "1800")),
            allow_public_signup=os.getenv("ALLOW_PUBLIC_SIGNUP", "0").strip().lower() in {"1", "true", "yes", "on"},
            audio_hot_limit=int(os.getenv("AUDIO_HOT_LIMIT", "200")),
            backup_snapshot_limit=int(os.getenv("BACKUP_SNAPSHOT_LIMIT", "10")),
            provider_timeout_seconds=float(os.getenv("PROVIDER_TEST_TIMEOUT_SECONDS", "10")),
            generation_timeout_seconds=float(os.getenv("GENERATION_TIMEOUT_SECONDS", "120")),
            max_json_body_bytes=max_json,
            max_upload_body_bytes=max(
                max_json,
                int(os.getenv("MAX_UPLOAD_BODY_BYTES", str(32 * 1024 * 1024))),
            ),
            max_tts_text_chars=max(1, int(os.getenv("MAX_TTS_TEXT_CHARS", "20000"))),
            interactive_workers=max(1, int(os.getenv("JOB_QUEUE_INTERACTIVE_WORKERS", "1"))),
            media_workers=max(1, int(os.getenv("JOB_QUEUE_MEDIA_WORKERS", "1"))),
            default_context_window_tokens=max(2048, int(os.getenv("DEFAULT_CONTEXT_WINDOW_TOKENS", "4096"))),
            context_summary_trigger_ratio=min(
                0.95,
                max(0.5, float(os.getenv("CONTEXT_SUMMARY_TRIGGER_RATIO", "0.75"))),
            ),
            context_max_compaction_passes=max(
                0,
                int(os.getenv("CONTEXT_MAX_COMPACTION_PASSES", "2")),
            ),
            memory_candidate_limit=min(10, max(1, int(os.getenv("MEMORY_CANDIDATE_LIMIT", "5")))),
            secure_cookies=_env_bool("NICE_ASSISTANT_SECURE_COOKIES", False),
            trust_proxy_headers=_env_bool("NICE_ASSISTANT_TRUST_PROXY_HEADERS", False),
            allowed_origins=_env_csv("NICE_ASSISTANT_ALLOWED_ORIGINS"),
            provider_allowed_hosts=_env_csv("NICE_ASSISTANT_PROVIDER_HOST_ALLOWLIST"),
            login_max_attempts=max(1, int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))),
            login_window_seconds=max(1, int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))),
            login_lockout_seconds=max(1, int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))),
            minimum_free_storage_bytes=max(0, int(os.getenv("MINIMUM_FREE_STORAGE_BYTES", str(128 * 1024 * 1024)))),
            audio_archive_retention_days=max(0, int(os.getenv("AUDIO_ARCHIVE_RETENTION_DAYS", "30"))),
            stt_recording_retention_days=max(0, int(os.getenv("STT_RECORDING_RETENTION_DAYS", "30"))),
            log_archive_retention_days=max(0, int(os.getenv("LOG_ARCHIVE_RETENTION_DAYS", "30"))),
            daily_database_backup_limit=max(1, int(os.getenv("DAILY_DATABASE_BACKUP_LIMIT", "14"))),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nice_assistant.db"

    @property
    def settings_json(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def video_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def stt_recordings_dir(self) -> Path:
        return self.data_dir / "stt_recordings"

    @property
    def identity_reference_dir(self) -> Path:
        return self.data_dir / "identity_references"

    @property
    def backup_dir(self) -> Path:
        return self.archive_dir / "backups"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.audio_dir,
            self.image_dir,
            self.video_dir,
            self.log_dir,
            self.stt_recordings_dir,
            self.identity_reference_dir,
            self.archive_dir,
            self.archive_dir / "audio",
            self.archive_dir / "logs",
            self.archive_dir / "db_backups",
            self.backup_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


class AppRuntime:
    def __init__(self, config: AppConfig, secret_store: SecretStore | None = None):
        self.config = config
        self.secret_store = secret_store or SecretStore()
        self.engine = build_engine(config.database_path)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.logger = logging.getLogger(f"nice-assistant.{id(self)}")
        self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        self.logger.propagate = False
        self.metrics = MetricsRegistry()
        self.file_handler: RotatingFileHandler | None = None

    def start(self) -> None:
        self.config.ensure_directories()
        initialize_database(
            self.config.database_path,
            self.config.session_ttl_seconds,
            secret_store=self.secret_store,
        )
        log_path = self.config.log_dir / "events.log"
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=8, encoding="utf-8")
        handler.setFormatter(RedactedJsonFormatter())
        self.logger.addHandler(handler)
        self.file_handler = handler

    def stop(self) -> None:
        if self.file_handler:
            self.logger.removeHandler(self.file_handler)
            self.file_handler.close()
            self.file_handler = None
        self.engine.dispose()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())
