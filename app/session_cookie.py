from fastapi import Response
from typing import Any

from app.resource_service import REMEMBERED_SESSION_SECONDS, AuthContext
from app.runtime import SESSION_COOKIE


def set_session_cookie(
    response: Response,
    context: AuthContext,
    ttl_seconds: int,
    *,
    secure: bool,
) -> None:
    options: dict[str, Any] = {
        "httponly": True,
        "samesite": "strict",
        "path": "/",
        "secure": secure,
    }
    if context.persistent:
        # Outlives closing the browser, which is the point of keeping a device.
        options["max_age"] = REMEMBERED_SESSION_SECONDS
    elif context.auto_logout:
        options["max_age"] = ttl_seconds
    response.set_cookie(SESSION_COOKIE, context.token, **options)
