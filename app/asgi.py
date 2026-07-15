from __future__ import annotations

from contextlib import asynccontextmanager
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from app.api_v1 import router as api_v1_router
from app.application import build_services
from app.identity_api import router as identity_api_router
from app.provider_contracts import ProviderError
from app.runtime import AppConfig
from app.security import SecurityObservabilityMiddleware
from app.service_errors import RateLimitError, ServiceError


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, json_limit: int, upload_limit: int):
        self.app = app
        self.json_limit = json_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        upload_paths = ("/api/v1/speech/transcriptions", "/visual-identity/references")
        limit = self.upload_limit if path == upload_paths[0] or path.endswith(upload_paths[1]) else self.json_limit
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > limit:
                    return await self._reject(scope, receive, send, path, limit)
            except ValueError:
                return await self._reject(scope, receive, send, path, limit, "invalid Content-Length")
        received = 0
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > limit:
                return await self._reject(scope, receive, send, path, limit)
            if not message.get("more_body", False):
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        return await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope, receive, send, _path, limit, message="request body too large"):
        content = {"error": {"code": 413, "message": message, "maxBytes": limit}}
        return await JSONResponse(status_code=413, content=content)(scope, receive, send)


def create_app(
    config: AppConfig | None = None,
    *,
    secret_store=None,
    providers=None,
    identity_providers=None,
    resource_providers=None,
    password_hasher=None,
    password_verifier=None,
) -> FastAPI:
    config = config or AppConfig.from_env()
    service_kwargs = {}
    if password_hasher is not None:
        service_kwargs["password_hasher"] = password_hasher
    if password_verifier is not None:
        service_kwargs["password_verifier"] = password_verifier
    app_services = build_services(
        config,
        secret_store=secret_store,
        providers=providers,
        identity_providers=identity_providers,
        resource_providers=resource_providers,
        **service_kwargs,
    )

    @asynccontextmanager
    async def lifespan(application):
        app_services.start()
        try:
            yield
        finally:
            app_services.stop()

    application = FastAPI(
        title="Nice Assistant API",
        version="1.0.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    application.state.services = app_services
    application.add_middleware(
        RequestBodyLimitMiddleware,
        json_limit=config.max_json_body_bytes,
        upload_limit=config.max_upload_body_bytes,
    )
    application.add_middleware(
        SecurityObservabilityMiddleware,
        allowed_origins=config.allowed_origins,
        metrics=app_services.runtime.metrics,
        logger=app_services.runtime.logger,
    )
    application.include_router(api_v1_router)
    application.include_router(identity_api_router)

    @application.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimitError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )

    @application.exception_handler(ProviderError)
    async def provider_error(_request: Request, exc: ProviderError):
        return JSONResponse(status_code=502, content={"error": exc.as_dict()})

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.status_code, "message": detail}},
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        issues = [
            {
                "field": ".".join(str(part) for part in item["loc"] if part != "body"),
                "message": item["msg"],
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": 422, "message": "request validation failed", "issues": issues}},
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, _exc: Exception):
        app_services.runtime.logger.exception("unhandled ASGI request error path=%s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": 500, "message": "internal server error"}},
        )

    @application.get("/health", tags=["system"])
    def health():
        return {"ok": True, "runtime": "asgi"}

    @application.get("/ready", tags=["system"])
    def ready():
        value = app_services.operations.readiness()
        return JSONResponse(status_code=200 if value["ready"] else 503, content=value)

    @application.get("/{path:path}", include_in_schema=False)
    def browser_files(path: str):
        relative = path or "index.html"
        if Path(relative).suffix.lower() not in {".html", ".js", ".css", ".svg", ".png", ".ico", ".webp"}:
            raise HTTPException(status_code=404, detail="not found")
        target = (config.web_dir / relative).resolve()
        web_root = config.web_dir.resolve()
        if target != web_root and web_root not in target.parents:
            raise HTTPException(status_code=404, detail="not found")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    return application


app = create_app()


def main():
    config = app.state.services.runtime.config
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "3000")),
        proxy_headers=config.trust_proxy_headers,
        forwarded_allow_ips=(os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1") if config.trust_proxy_headers else ""),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
