from fastapi import Response

from app.resource_service import AuthContext
from app.runtime import SESSION_COOKIE


def set_session_cookie(
    response: Response,
    context: AuthContext,
    ttl_seconds: int,
    *,
    secure: bool,
) -> None:
    options = {
        "httponly": True,
        "samesite": "strict",
        "path": "/",
        "secure": secure,
    }
    if context.auto_logout:
        options["max_age"] = ttl_seconds
    response.set_cookie(SESSION_COOKIE, context.token, **options)
