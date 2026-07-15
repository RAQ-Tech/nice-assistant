from __future__ import annotations

import json
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.identity_contracts import IdentityVerificationRequest, IdentityVerificationResult
from app.provider_contracts import CancellationToken, ProviderError, ProviderHealth, ProviderStatus


def normalize_compreface_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("CompreFace URL must be an http or https service URL.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("CompreFace URL cannot contain credentials, a query, or a fragment.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _multipart(source: bytes, target: bytes, source_name: str, target_name: str) -> tuple[bytes, str]:
    boundary = f"nice-assistant-{secrets.token_hex(12)}"
    body = bytearray()
    for field, filename, content in (
        ("source_image", source_name, source),
        ("target_image", target_name, target),
    ):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode())
        body.extend(b"Content-Type: image/jpeg\r\n\r\n")
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class CompreFaceIdentityProvider:
    """Stateless two-image verification; Nice Assistant does not enroll faces in CompreFace."""

    name = "compreface"

    def health(self, base_url: str, api_key: str, timeout_seconds: float) -> ProviderHealth:
        started = time.monotonic()
        try:
            url = self._verification_url(base_url)
            body, content_type = _multipart(b"", b"", "empty.jpg", "empty.jpg")
            request = Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": content_type, "x-api-key": api_key, "Accept": "application/json"},
            )
            with urlopen(request, timeout=timeout_seconds):
                pass
            status = ProviderStatus.READY
            message = "CompreFace verification endpoint is reachable."
        except HTTPError as exc:
            if exc.code in {400, 415, 422}:
                status = ProviderStatus.READY
                message = "CompreFace verification endpoint is reachable with the configured request."
            elif exc.code in {401, 403}:
                status = ProviderStatus.UNAVAILABLE
                message = "CompreFace rejected the configured API key."
            else:
                status = ProviderStatus.UNAVAILABLE
                message = "CompreFace verification endpoint is unavailable."
        except (OSError, URLError, ValueError):
            status = ProviderStatus.UNAVAILABLE
            message = "CompreFace verification endpoint is unavailable."
        return ProviderHealth(self.name, status, message, int((time.monotonic() - started) * 1000))

    def verify(
        self,
        request: IdentityVerificationRequest,
        cancellation: CancellationToken,
    ) -> IdentityVerificationResult:
        cancellation.raise_if_cancelled()
        body, content_type = _multipart(
            request.source_content,
            request.target_content,
            request.source_filename,
            request.target_filename,
        )
        url = self._verification_url(request.base_url)
        http_request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": content_type,
                "x-api-key": request.api_key,
                "Accept": "application/json",
            },
        )
        response = None
        try:
            response = urlopen(http_request, timeout=request.timeout_seconds)
            cancellation.register(response.close)
            raw = response.read(2 * 1024 * 1024)
            cancellation.raise_if_cancelled()
            payload = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raise self._provider_error(exc.code) from exc
        except (TimeoutError, URLError) as exc:
            raise ProviderError(
                provider=self.name,
                code="identity_provider_unavailable",
                user_message="The visual identity verifier is unavailable.",
                retryable=True,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                provider=self.name,
                code="identity_provider_response_invalid",
                user_message="The visual identity verifier returned an invalid response.",
            ) from exc
        finally:
            if response is not None:
                response.close()
        return self._parse(payload)

    @staticmethod
    def _verification_url(base_url: str) -> str:
        base = normalize_compreface_base_url(base_url)
        query = urlencode({"limit": 1, "det_prob_threshold": 0.8})
        return f"{base}/api/v1/verification/verify?{query}"

    def _provider_error(self, status: int) -> ProviderError:
        if status in {401, 403}:
            return ProviderError(
                provider=self.name,
                code="identity_provider_auth_failed",
                user_message="The visual identity verifier rejected its configured credentials.",
            )
        if status in {400, 404, 415, 422}:
            return ProviderError(
                provider=self.name,
                code="identity_face_not_detected",
                user_message="The verifier could not find one clear face in both images.",
            )
        return ProviderError(
            provider=self.name,
            code="identity_provider_failed",
            user_message="The visual identity verifier could not compare the images.",
            retryable=status >= 500,
        )

    def _parse(self, payload) -> IdentityVerificationResult:
        results = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise self._provider_error(422)
        similarities: list[float] = []
        target_count = 0
        for item in results:
            matches = item.get("face_matches") if isinstance(item, dict) else None
            if isinstance(matches, list):
                target_count += len(matches)
                for match in matches:
                    try:
                        similarities.append(float(match.get("similarity")))
                    except (AttributeError, TypeError, ValueError):
                        continue
        if not similarities:
            raise self._provider_error(422)
        return IdentityVerificationResult(
            similarity=max(0.0, min(1.0, max(similarities))),
            source_face_count=len(results),
            target_face_count=target_count,
            provider_version=str(payload.get("plugins_versions") or "") or None,
        )
