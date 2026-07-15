class ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code or str(status_code)


class NotFoundError(ServiceError):
    def __init__(self, message="not found"):
        super().__init__(404, message, "not_found")


class AuthenticationError(ServiceError):
    def __init__(self, message="unauthorized"):
        super().__init__(401, message, "unauthorized")


class AuthorizationError(ServiceError):
    def __init__(self, message="forbidden"):
        super().__init__(403, message, "forbidden")


class ConflictError(ServiceError):
    def __init__(self, message="conflict"):
        super().__init__(409, message, "conflict")


class RequestError(ServiceError):
    def __init__(self, message="invalid request", status_code=422):
        super().__init__(status_code, message, "invalid_request")


class RateLimitError(ServiceError):
    def __init__(self, retry_after: int):
        super().__init__(429, "Too many login attempts. Try again later.", "rate_limited")
        self.retry_after = max(1, int(retry_after))


class StorageCapacityError(ServiceError):
    def __init__(self):
        super().__init__(507, "Storage is unavailable or full. Free space and retry.", "storage_unavailable")


class InvalidArtifactError(ServiceError):
    def __init__(self):
        super().__init__(502, "The provider returned an empty or invalid artifact.", "invalid_artifact")
