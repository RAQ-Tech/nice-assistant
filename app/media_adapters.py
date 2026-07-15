from __future__ import annotations

from app.media_clients import automatic1111_image, comfyui_image, openai_image, openai_video
from app.provider_contracts import (
    CancellationToken,
    MediaArtifact,
    MediaRequest,
    ProviderHealth,
    ProviderStatus,
)


class OpenAIImageProvider:
    name = "openai-image"

    def health(self):
        return ProviderHealth(self.name, ProviderStatus.DEGRADED, "Use the authenticated provider check.")

    def generate(self, request: MediaRequest, cancellation: CancellationToken) -> MediaArtifact:
        cancellation.raise_if_cancelled()
        content = openai_image(
            request.prompt,
            request.options.get("size"),
            request.options.get("quality"),
            request.options.get("api_key"),
        )
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", content, ".png", "image/png")


class LocalImageProvider:
    name = "local-image"

    def health(self):
        return ProviderHealth(self.name, ProviderStatus.DEGRADED, "Use the selected backend provider check.")

    def generate(self, request: MediaRequest, cancellation: CancellationToken) -> MediaArtifact:
        cancellation.raise_if_cancelled()
        options = request.options
        backend = options.get("backend") or "automatic1111"
        operation = options.get("operation") or "generate"
        if backend == "comfyui":
            content = comfyui_image(
                request.prompt,
                options.get("size"),
                options.get("quality"),
                bool(options.get("allow_nsfw")),
                options.get("base_url"),
                options.get("local_settings"),
                cancellation,
            )
        else:
            if operation != "generate":
                raise ValueError("Automatic1111 editing is not implemented by this adapter")
            content = automatic1111_image(
                request.prompt,
                options.get("size"),
                options.get("quality"),
                bool(options.get("allow_nsfw")),
                options.get("base_url"),
                options.get("local_settings"),
            )
        cancellation.raise_if_cancelled()
        return MediaArtifact("image", content, ".png", "image/png")


class OpenAIVideoProvider:
    name = "openai-video"

    def health(self):
        return ProviderHealth(self.name, ProviderStatus.DEGRADED, "Use the authenticated provider check.")

    def generate(self, request: MediaRequest, cancellation: CancellationToken) -> MediaArtifact:
        cancellation.raise_if_cancelled()
        content, extension = openai_video(
            request.prompt,
            request.options.get("size"),
            request.options.get("seconds"),
            request.options.get("api_key"),
            model=request.options.get("model"),
            input_reference=request.options.get("input_reference"),
        )
        cancellation.raise_if_cancelled()
        content_type = (
            "video/webm" if extension == ".webm" else ("video/quicktime" if extension == ".mov" else "video/mp4")
        )
        return MediaArtifact("video", content, extension, content_type)
