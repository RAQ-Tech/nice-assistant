from __future__ import annotations


class ProviderRegistry:
    def __init__(self, chat_providers=None, media_providers=None, task_providers=None):
        self.chat_providers = dict(chat_providers or {})
        self.media_providers = dict(media_providers or {})
        # Task roles emit structured data under an enforced schema; conversation is a
        # separate product decision. A provider may serve one without serving the other,
        # so the sets are configured separately and default to the chat providers.
        self.task_providers = dict(task_providers) if task_providers is not None else dict(self.chat_providers)

    def chat(self, name: str):
        try:
            return self.chat_providers[name]
        except KeyError as exc:
            raise LookupError(f"chat provider not configured: {name}") from exc

    def task(self, name: str):
        try:
            return self.task_providers[name]
        except KeyError as exc:
            raise LookupError(f"task provider not configured: {name}") from exc

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
