from __future__ import annotations

from collections import deque
import hashlib
import ipaddress
import re
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi.responses import JSONResponse

from app.observability import new_request_id, request_id_context
from app.service_errors import RateLimitError


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-nice-assistant-csrf"
CSRF_HEADER_VALUE = "1"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_PRIVATE_PROVIDER_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("::1/128"),
)
_KNOWN_CONTAINER_HOSTS = {"ollama", "comfyui", "automatic1111", "kokoro", "compreface"}


def normalize_origin(value: str) -> str:
    parts = urlsplit(str(value or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("origin must be an HTTP or HTTPS origin")
    if parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("origin must not contain credentials, a path, a query, or a fragment")
    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 80 if parts.scheme == "http" else 443
    port = f":{parts.port}" if parts.port and parts.port != default_port else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def normalize_request_id(value: str | None, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else fallback


class ProviderUrlPolicy:
    """Allow outbound calls only to explicit or recognizably private LAN services."""

    def __init__(self, allowed_hosts: tuple[str, ...] = ()):
        self.allowed_hosts = {str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip()}

    def normalize(self, value: str, *, label: str = "Provider") -> str:
        raw = str(value or "").strip().rstrip("/")
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"{label} URL must be an HTTP or HTTPS service URL.")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(f"{label} URL cannot contain credentials, a query, or a fragment.")
        try:
            port = parts.port
        except ValueError as exc:
            raise ValueError(f"{label} URL has an invalid port.") from exc
        host = parts.hostname.lower().rstrip(".")
        if not self._host_allowed(host):
            raise ValueError(
                f"{label} host is outside the private-LAN provider policy. "
                "Add it to NICE_ASSISTANT_PROVIDER_HOST_ALLOWLIST only if it is trusted."
            )
        display_host = f"[{host}]" if ":" in host else host
        netloc = f"{display_host}:{port}" if port else display_host
        return urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/"), "", ""))

    def _host_allowed(self, host: str) -> bool:
        if host in self.allowed_hosts:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return (
                host == "localhost"
                or host.endswith(".localhost")
                or host.endswith(".lan")
                or host.endswith(".local")
                or host == "host.docker.internal"
                or host in _KNOWN_CONTAINER_HOSTS
            )
        return any(address.version == network.version and address in network for network in _PRIVATE_PROVIDER_RANGES)


class LoginThrottle:
    def __init__(self, *, max_attempts: int, window_seconds: int, lockout_seconds: int, clock=time.monotonic):
        self.max_attempts = max(1, int(max_attempts))
        self.window_seconds = max(1, int(window_seconds))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self.clock = clock
        self._attempts: dict[str, deque[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(client_address: str, username: str) -> str:
        identity = f"{str(client_address or 'unknown').lower()}\0{str(username or '').strip().casefold()}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def check(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._prune(key, now)
            locked_until = self._locked_until.get(key, 0)
            if locked_until > now:
                raise RateLimitError(max(1, int(locked_until - now + 0.999)))

    def failure(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            self._prune(key, now)
            attempts = self._attempts.setdefault(key, deque())
            attempts.append(now)
            if len(attempts) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds
                attempts.clear()

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._locked_until.pop(key, None)

    def _prune(self, key: str, now: float) -> None:
        attempts = self._attempts.get(key)
        if attempts is not None:
            cutoff = now - self.window_seconds
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(key, None)
        if self._locked_until.get(key, 0) <= now:
            self._locked_until.pop(key, None)


def request_client_address(request, *, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded[:128]
    return str(request.client.host if request.client else "unknown")[:128]


class SecurityObservabilityMiddleware:
    def __init__(self, app, *, allowed_origins: tuple[str, ...], metrics, logger):
        self.app = app
        self.allowed_origins = {normalize_origin(value) for value in allowed_origins}
        self.metrics = metrics
        self.logger = logger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = normalize_request_id(
            headers.get(b"x-request-id", b"").decode("latin-1"),
            new_request_id(),
        )
        token = request_id_context.set(request_id)
        started = time.monotonic()
        status = 500
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        try:
            if method in UNSAFE_METHODS and path.startswith("/api/v1"):
                rejection = self._unsafe_rejection(scope, headers)
                if rejection:
                    status = 403
                    response = JSONResponse(
                        status_code=403,
                        content={"error": {"code": "csrf_rejected", "message": rejection}},
                    )

                    async def rejection_send(message):
                        if message["type"] == "http.response.start":
                            response_headers = list(message.get("headers", []))
                            response_headers.extend(self._response_headers(path, request_id))
                            message["headers"] = response_headers
                        await send(message)

                    return await response(scope, receive, rejection_send)

            async def observed_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = int(message.get("status", 500))
                    response_headers = list(message.get("headers", []))
                    response_headers.extend(self._response_headers(path, request_id))
                    message["headers"] = response_headers
                await send(message)

            return await self.app(scope, receive, observed_send)
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            self.metrics.request(method, status, latency_ms)
            self.logger.info(
                "http.request method=%s path=%s status=%s latency_ms=%s",
                method,
                path[:500],
                status,
                latency_ms,
            )
            request_id_context.reset(token)

    def _unsafe_rejection(self, scope, headers: dict[bytes, bytes]) -> str | None:
        if headers.get(CSRF_HEADER.encode(), b"").decode("latin-1") != CSRF_HEADER_VALUE:
            return "State-changing requests require the Nice Assistant CSRF header."
        origin = headers.get(b"origin", b"").decode("latin-1").strip()
        if not origin:
            return None
        try:
            source = normalize_origin(origin)
        except ValueError:
            return "Request origin is invalid."
        if self.allowed_origins:
            return None if source in self.allowed_origins else "Request origin is not allowed."
        host = headers.get(b"host", b"").decode("latin-1").strip()
        if not host:
            return "Request target origin is unavailable."
        try:
            target = normalize_origin(f"{scope.get('scheme', 'http')}://{host}")
        except ValueError:
            return "Request target origin is invalid."
        return None if source == target else "Request origin does not match the target origin."

    @staticmethod
    def _response_headers(path: str, request_id: str) -> list[tuple[bytes, bytes]]:
        headers = [
            (b"x-request-id", request_id.encode()),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"no-referrer"),
            (b"cross-origin-resource-policy", b"same-origin"),
            (b"permissions-policy", b"camera=(), geolocation=(), microphone=(self)"),
        ]
        if path.startswith("/api/"):
            headers.append((b"cache-control", b"no-store"))
        return headers
