from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from app.provider_contracts import CapacityStatus, ProviderCapacitySnapshot
from app.repositories import UnitOfWork
from app.resource_providers import (
    Automatic1111ResourceProvider,
    ComfyUIResourceProvider,
    OllamaResourceProvider,
)


def normalize_endpoint(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("provider endpoint must be an HTTP or HTTPS URL")
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), f"{host}{port}", path, "", ""))


def endpoint_fingerprint(provider: str, endpoint: str) -> str:
    normalized = normalize_endpoint(endpoint)
    return hashlib.sha256(f"{provider}:{normalized}".encode()).hexdigest()[:24]


def endpoint_label(endpoint: str) -> str:
    parts = urlsplit(normalize_endpoint(endpoint))
    return parts.netloc


@dataclass(frozen=True)
class ResourceRequest:
    user_id: str
    provider: str
    endpoint: str
    api_auth: str | None
    estimated_vram_mb: int

    @property
    def fingerprint(self) -> str:
        return endpoint_fingerprint(self.provider, self.endpoint)


@dataclass
class AdmissionRecord:
    job_id: str
    request: ResourceRequest
    started_monotonic: float
    next_check_monotonic: float
    state: str = "checking"
    waiting_recorded: bool = False
    control_attempted: bool = False
    execution_started: bool = False
    cancellation_recorded: bool = False
    snapshot: ProviderCapacitySnapshot | None = None
    on_wait: object | None = None
    on_reject: object | None = None


class ResourceCoordinator:
    def __init__(
        self,
        session_factory,
        secret_store,
        config,
        logger,
        providers: dict | None = None,
        provider_url_policy=None,
    ):
        self.session_factory = session_factory
        self.secret_store = secret_store
        self.config = config
        self.logger = logger
        self.providers = providers or {
            "ollama": OllamaResourceProvider(config.provider_timeout_seconds),
            "comfyui": ComfyUIResourceProvider(config.provider_timeout_seconds),
            "automatic1111": Automatic1111ResourceProvider(config.provider_timeout_seconds),
        }
        self.provider_url_policy = provider_url_policy
        self._policy = {
            "mode": "disabled",
            "reserve_vram_mb": 1024,
            "max_wait_seconds": 300,
            "poll_interval_seconds": 2.0,
        }
        self._records: dict[str, AdmissionRecord] = {}
        self._active_job_id: str | None = None
        self._control_in_progress = False
        self._wake_queue = lambda: None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _uow(self):
        return UnitOfWork(self.session_factory, self.secret_store)

    def start(self) -> None:
        with self._uow() as uow:
            self._policy = self._setting_response(uow.repo.resource_coordination_setting())
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="resource-coordinator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        with self._lock:
            self._records.clear()
            self._active_job_id = None
            self._control_in_progress = False

    def bind_queue_wake(self, callback) -> None:
        self._wake_queue = callback or (lambda: None)

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._policy["mode"] != "disabled"

    def request_for_media(
        self, user_id: str, kind: str, values: dict, estimated_vram_mb: int
    ) -> ResourceRequest | None:
        if kind != "image" or not self.enabled:
            return None
        with self._uow() as uow:
            settings = uow.repo.settings(user_id) or {}
        preferences = settings.get("preferences") if isinstance(settings.get("preferences"), dict) else {}
        selected = str(values.get("provider") or preferences.get("image_provider") or "disabled").lower()
        backend_override = None
        if selected == "local/automatic1111":
            selected, backend_override = "local", "automatic1111"
        elif selected == "local/comfyui":
            selected, backend_override = "local", "comfyui"
        if selected != "local":
            return None
        backend = str(
            backend_override or values.get("backend") or preferences.get("image_local_backend") or "automatic1111"
        ).lower()
        provider = "comfyui" if backend == "comfyui" else "automatic1111"
        default_endpoint = self.config.comfyui_base_url if provider == "comfyui" else self.config.automatic1111_base_url
        endpoint = normalize_endpoint(
            values.get("base_url") or preferences.get("image_local_base_url") or default_endpoint
        )
        if self.provider_url_policy:
            endpoint = self.provider_url_policy.normalize(endpoint, label=f"{provider} resource service")
        return ResourceRequest(
            user_id=user_id,
            provider=provider,
            endpoint=endpoint,
            api_auth=preferences.get("image_local_api_auth"),
            estimated_vram_mb=max(0, int(estimated_vram_mb or 0)),
        )

    def register(self, job_id: str, request: ResourceRequest | None, *, on_wait=None, on_reject=None) -> None:
        if request is None or not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._records[job_id] = AdmissionRecord(
                job_id=job_id,
                request=request,
                started_monotonic=now,
                next_check_monotonic=now,
                # Unknown demand cannot pass measured-capacity admission, but
                # the job still needs a lease and lifecycle record so an
                # explicitly managed media provider can be reclaimed afterward.
                state="checking" if request.estimated_vram_mb > 0 else "admitted",
                on_wait=on_wait,
                on_reject=on_reject,
            )
        self._wake.set()

    def can_start(self, job) -> bool:
        if not bool(job.metadata.get("coordinated_resource")):
            return True
        with self._lock:
            # A release request already in flight remains a critical section even
            # if an administrator disables future coordination mid-request.
            if self._control_in_progress:
                return False
            if self._policy["mode"] == "disabled":
                return True
            if self._active_job_id and self._active_job_id != job.id:
                return False
            record = self._records.get(job.metadata.get("async_job_id"))
            return record is None or record.state == "admitted"

    def reserve(self, job) -> None:
        if not bool(job.metadata.get("coordinated_resource")) or not self.enabled:
            return
        with self._lock:
            self._active_job_id = job.id

    def execution_started(self, async_job_id: str) -> None:
        with self._lock:
            record = self._records.get(async_job_id)
            if record:
                record.execution_started = True

    def complete(self, queue_job_id: str, async_job_id: str) -> None:
        cleanup_record = None
        with self._lock:
            was_active = self._active_job_id == queue_job_id
            if was_active:
                self._active_job_id = None
            record = self._records.pop(async_job_id, None)
            if was_active and record and record.execution_started and self._policy["mode"] == "managed":
                # Keep the queue lease closed until the coarse provider release
                # finishes. Otherwise a waiting chat can start in the narrow
                # window between media completion and reclamation.
                self._control_in_progress = True
                cleanup_record = record
        if cleanup_record:
            try:
                try:
                    self._release_provider(
                        cleanup_record,
                        cleanup_record.request.provider,
                        cleanup_record.request.endpoint,
                        cleanup_record.request.api_auth,
                        trigger="post_job",
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup must not mask the completed job
                    self.logger.warning(
                        "post-job resource cleanup failed provider=%s job_id=%s error=%s",
                        cleanup_record.request.provider,
                        cleanup_record.job_id,
                        exc.__class__.__name__,
                    )
            finally:
                with self._lock:
                    self._control_in_progress = False
        self._wake.set()
        self._wake_queue()

    def cancel(self, job_id: str) -> None:
        record = None
        record_event = False
        with self._lock:
            record = self._records.get(job_id)
            if record:
                record_event = not record.cancellation_recorded
                record.cancellation_recorded = True
                if not record.execution_started:
                    self._records.pop(job_id, None)
        if record and record_event:
            self._event(record, "cancelled", "cancelled", {})
        self._wake.set()
        self._wake_queue()

    def policy(self) -> dict:
        with self._lock:
            return dict(self._policy)

    def update(self, admin_user_id: str, values: dict) -> dict:
        with self._uow() as uow:
            row = uow.repo.save_resource_coordination_setting(values)
            policy = self._setting_response(row)
            endpoints = self._endpoint_specs(uow.repo, admin_user_id)
            by_provider = {
                str(item.get("provider")): item for item in values.get("authorizations", []) if isinstance(item, dict)
            }
            for spec in endpoints:
                submitted = by_provider.get(spec["provider"])
                if not submitted:
                    continue
                uow.repo.save_resource_control_authorization(
                    provider=spec["provider"],
                    endpoint_fingerprint=spec["fingerprint"],
                    exclusive_control=bool(submitted.get("exclusive_control")),
                    allow_release=bool(submitted.get("exclusive_control") and submitted.get("allow_release")),
                    authorized_by_user_id=admin_user_id,
                )
        with self._lock:
            self._policy = policy
            if policy["mode"] == "disabled":
                # Disabling coordination must restore the historical non-blocking
                # behavior immediately and must not let the polling thread reject
                # work that the queue is now allowed to start.
                for record in self._records.values():
                    record.state = "admitted"
        self._wake.set()
        self._wake_queue()
        return self.status(admin_user_id, refresh=False)

    def status(self, admin_user_id: str, *, refresh: bool = True) -> dict:
        with self._uow() as uow:
            endpoints = self._endpoint_specs(uow.repo, admin_user_id)
            for spec in endpoints:
                auth = uow.repo.resource_control_authorization(spec["provider"], spec["fingerprint"])
                spec["authorization"] = self._authorization_response(auth)
        if refresh:
            with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
                snapshots = list(pool.map(lambda spec: self._snapshot_spec(spec), endpoints))
            for spec, snapshot in zip(endpoints, snapshots, strict=True):
                spec["snapshot"] = self._snapshot_response(snapshot)
                spec["capabilities"] = self._capabilities_response(
                    self.providers[spec["provider"]].capabilities(spec["endpoint"], spec.get("api_auth"))
                )
        else:
            for spec in endpoints:
                spec["snapshot"] = None
                spec["capabilities"] = self._capabilities_response(
                    self.providers[spec["provider"]].capabilities(spec["endpoint"], spec.get("api_auth"))
                )
        for spec in endpoints:
            spec.pop("api_auth", None)
            spec.pop("endpoint", None)
        return {"settings": self.policy(), "endpoints": endpoints}

    def events(self, limit: int = 100) -> list[dict]:
        with self._uow() as uow:
            rows = uow.repo.resource_coordination_events(limit)
            result = []
            for row in rows:
                try:
                    detail = json.loads(row.detail_json or "{}")
                except (TypeError, ValueError):
                    detail = {}
                result.append(
                    {
                        "id": row.id,
                        "job_id": row.job_id,
                        "provider": row.provider,
                        "endpoint_fingerprint": row.endpoint_fingerprint,
                        "action": row.action,
                        "outcome": row.outcome,
                        "detail": detail if isinstance(detail, dict) else {},
                        "created_at": row.created_at,
                    }
                )
            return result

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                due = [
                    row for row in self._records.values() if row.state == "checking" and row.next_check_monotonic <= now
                ]
                interval = float(self._policy["poll_interval_seconds"])
            for record in due:
                self._process(record)
            self._wake.wait(timeout=max(0.1, min(interval, 1.0)))
            self._wake.clear()

    def _process(self, record: AdmissionRecord) -> None:
        with self._lock:
            if self._records.get(record.job_id) is not record:
                return
            policy = dict(self._policy)
        snapshot = self.providers[record.request.provider].snapshot(record.request.endpoint, record.request.api_auth)
        with self._lock:
            if self._records.get(record.job_id) is not record or record.state != "checking":
                return
            policy = dict(self._policy)
            if policy["mode"] == "disabled":
                return
        elapsed = time.monotonic() - record.started_monotonic
        required = record.request.estimated_vram_mb + int(policy["reserve_vram_mb"])
        if snapshot.status is CapacityStatus.KNOWN and snapshot.free_vram_mb is not None:
            if snapshot.free_vram_mb >= required:
                with self._lock:
                    current = self._records.get(record.job_id)
                    if current is not record:
                        return
                    record.snapshot = snapshot
                    record.state = "admitted"
                self._event(
                    record,
                    "admitted",
                    "success",
                    {"required_vram_mb": required, "free_vram_mb": snapshot.free_vram_mb},
                )
                self._wake_queue()
                return
        if elapsed >= int(policy["max_wait_seconds"]):
            with self._lock:
                if self._records.get(record.job_id) is not record:
                    return
                # Keep a non-admissible record until the rejection callback has
                # synchronously removed the queue entry. Removing it first would
                # let a periodically waking worker mistake "missing" for admitted.
                record.state = "rejected"
            self._event(
                record,
                "timed_out",
                "failed",
                {
                    "required_vram_mb": required,
                    "capacity_status": snapshot.status.value,
                    "free_vram_mb": snapshot.free_vram_mb,
                },
            )
            if record.on_reject:
                record.on_reject("gpu_capacity_timeout", "GPU capacity did not become available before the wait limit.")
            with self._lock:
                self._records.pop(record.job_id, None)
            self._wake_queue()
            return
        if not record.waiting_recorded:
            record.waiting_recorded = True
            self._event(
                record,
                "waiting",
                "info",
                {
                    "required_vram_mb": required,
                    "capacity_status": snapshot.status.value,
                    "free_vram_mb": snapshot.free_vram_mb,
                },
            )
            if record.on_wait:
                record.on_wait("Waiting for GPU capacity")
        if policy["mode"] == "managed" and not record.control_attempted:
            record.control_attempted = True
            self._attempt_release(record)
        record.snapshot = snapshot
        record.next_check_monotonic = time.monotonic() + float(policy["poll_interval_seconds"])

    def _attempt_release(self, record: AdmissionRecord) -> None:
        with self._lock:
            if self._active_job_id or self._control_in_progress:
                record.control_attempted = False
                return
            self._control_in_progress = True
        try:
            targets = [
                (record.request.provider, record.request.endpoint, record.request.api_auth),
                ("ollama", normalize_endpoint(self.config.ollama_base_url), None),
            ]
            for provider, endpoint, api_auth in targets:
                self._release_provider(record, provider, endpoint, api_auth, trigger="pre_admission")
        finally:
            with self._lock:
                self._control_in_progress = False
            self._wake_queue()

    def _release_provider(
        self,
        record: AdmissionRecord,
        provider: str,
        endpoint: str,
        api_auth: str | None,
        *,
        trigger: str,
    ) -> bool:
        fingerprint = endpoint_fingerprint(provider, endpoint)
        try:
            with self._uow() as uow:
                authorization = uow.repo.resource_control_authorization(provider, fingerprint)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the completed job
            self.logger.warning(
                "resource authorization lookup failed provider=%s job_id=%s error=%s",
                provider,
                record.job_id,
                exc.__class__.__name__,
            )
            return False
        if not authorization or not authorization.exclusive_control or not authorization.allow_release:
            return False
        try:
            detail = self.providers[provider].release(endpoint, api_auth)
        except Exception as exc:
            self.logger.warning(
                "resource release failed provider=%s job_id=%s error=%s",
                provider,
                record.job_id,
                exc.__class__.__name__,
            )
            self._event_for(
                job_id=record.job_id,
                user_id=record.request.user_id,
                provider=provider,
                fingerprint=fingerprint,
                action="release_failed",
                outcome="failed",
                detail={"code": "provider_release_failed", "trigger": trigger},
            )
            return False
        self._event_for(
            job_id=record.job_id,
            user_id=record.request.user_id,
            provider=provider,
            fingerprint=fingerprint,
            action="released",
            outcome="success",
            detail={
                "scope": detail.get("scope"),
                "model_count": detail.get("model_count"),
                "trigger": trigger,
            },
        )
        return True

    def _endpoint_specs(self, repo, user_id: str) -> list[dict]:
        settings = repo.settings(user_id) or {}
        preferences = settings.get("preferences") if isinstance(settings.get("preferences"), dict) else {}
        configured_local = preferences.get("image_local_base_url")
        api_auth = preferences.get("image_local_api_auth")
        values = [
            ("ollama", self.config.ollama_base_url, None),
            (
                "automatic1111",
                configured_local
                if preferences.get("image_local_backend") == "automatic1111" and configured_local
                else self.config.automatic1111_base_url,
                api_auth,
            ),
            (
                "comfyui",
                configured_local
                if preferences.get("image_local_backend") == "comfyui" and configured_local
                else self.config.comfyui_base_url,
                api_auth,
            ),
        ]
        return [
            {
                "provider": provider,
                "endpoint": normalize_endpoint(endpoint),
                "endpoint_label": endpoint_label(endpoint),
                "fingerprint": endpoint_fingerprint(provider, endpoint),
                "api_auth": auth,
            }
            for provider, endpoint, auth in values
        ]

    def _snapshot_spec(self, spec: dict) -> ProviderCapacitySnapshot:
        return self.providers[spec["provider"]].snapshot(spec["endpoint"], spec.get("api_auth"))

    def _event(self, record: AdmissionRecord, action: str, outcome: str, detail: dict) -> None:
        self._event_for(
            job_id=record.job_id,
            user_id=record.request.user_id,
            provider=record.request.provider,
            fingerprint=record.request.fingerprint,
            action=action,
            outcome=outcome,
            detail=detail,
        )

    def _event_for(
        self,
        *,
        job_id: str,
        user_id: str,
        provider: str,
        fingerprint: str,
        action: str,
        outcome: str,
        detail: dict,
    ) -> None:
        safe_detail = {key: value for key, value in detail.items() if value is not None}
        with self._uow() as uow:
            uow.repo.add_resource_coordination_event(
                job_id=job_id,
                user_id=user_id,
                provider=provider,
                endpoint_fingerprint=fingerprint,
                action=action,
                outcome=outcome,
                detail=safe_detail,
            )

    @staticmethod
    def _setting_response(row) -> dict:
        return {
            "mode": row.mode,
            "reserve_vram_mb": row.reserve_vram_mb,
            "max_wait_seconds": row.max_wait_seconds,
            "poll_interval_seconds": row.poll_interval_seconds,
        }

    @staticmethod
    def _authorization_response(row) -> dict:
        return {
            "exclusive_control": bool(row.exclusive_control) if row else False,
            "allow_release": bool(row.allow_release) if row else False,
            "authorized_at": row.updated_at if row else None,
        }

    @staticmethod
    def _snapshot_response(snapshot: ProviderCapacitySnapshot) -> dict:
        return {
            "status": snapshot.status.value,
            "source": snapshot.source,
            "observed_at": snapshot.observed_at,
            "total_vram_mb": snapshot.total_vram_mb,
            "free_vram_mb": snapshot.free_vram_mb,
            "queue_depth": snapshot.queue_depth,
            "active_jobs": snapshot.active_jobs,
            "loaded_models": list(snapshot.loaded_models),
            "message": snapshot.message,
        }

    @staticmethod
    def _capabilities_response(value) -> dict:
        return {
            "reports_capacity": value.reports_capacity,
            "reports_queue": value.reports_queue,
            "supports_release": value.supports_release,
            "supports_precise_cancel": value.supports_precise_cancel,
        }
