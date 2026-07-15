from __future__ import annotations


class ProviderRegistry:
    def __init__(self, chat_providers=None, media_providers=None):
        self.chat_providers = dict(chat_providers or {})
        self.media_providers = dict(media_providers or {})

    def chat(self, name: str):
        try:
            return self.chat_providers[name]
        except KeyError as exc:
            raise LookupError(f"chat provider not configured: {name}") from exc

    def media(self, name: str):
        try:
            return self.media_providers[name]
        except KeyError as exc:
            raise LookupError(f"media provider not configured: {name}") from exc

    def models(self) -> list[str]:
        result = []
        for provider in self.chat_providers.values():
            result.extend(provider.list_models())
        return list(dict.fromkeys(result))
