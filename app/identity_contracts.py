from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.provider_contracts import CancellationToken, ProviderHealth


@dataclass(frozen=True)
class IdentityVerificationRequest:
    base_url: str
    api_key: str
    timeout_seconds: float
    source_content: bytes
    target_content: bytes
    source_filename: str = "reference.jpg"
    target_filename: str = "candidate.jpg"


@dataclass(frozen=True)
class IdentityVerificationResult:
    similarity: float
    source_face_count: int
    target_face_count: int
    provider_version: str | None = None
    request_id: str | None = None


class IdentityVerificationProvider(Protocol):
    name: str

    def health(self, base_url: str, api_key: str, timeout_seconds: float) -> ProviderHealth: ...

    def verify(
        self,
        request: IdentityVerificationRequest,
        cancellation: CancellationToken,
    ) -> IdentityVerificationResult: ...
