from __future__ import annotations

from dataclasses import dataclass


_OPEN_SYSTEM_PROMPT = "[SYSTEM_PROMPT]"
_CLOSE_SYSTEM_PROMPT = "[/SYSTEM_PROMPT]"
_OUTSIDE_MARKERS = (_OPEN_SYSTEM_PROMPT, _CLOSE_SYSTEM_PROMPT)
PERSONA_OUTPUT_REMOVED_FALLBACK = "Sorry, something went wrong with that reply. Please try again."


@dataclass(frozen=True)
class SanitizedPersonaOutput:
    """Safe text emitted so far and whether a protected envelope was removed."""

    text: str
    protected_content_removed: bool


def _longest_marker_prefix_suffix(value: str, markers: tuple[str, ...]) -> int:
    """Return the suffix length that could become a marker in a later chunk."""

    folded = value.casefold()
    longest = 0
    for marker in markers:
        marker_folded = marker.casefold()
        maximum = min(len(folded), len(marker_folded) - 1)
        for length in range(maximum, longest, -1):
            if folded.endswith(marker_folded[:length]):
                longest = length
                break
    return longest


class PersonaOutputStreamFilter:
    """Remove protected system-prompt envelopes without leaking split markers.

    `feed` and `finish` return only text that became safe during that call. The
    removal flag is cumulative, so callers can inspect any returned result (or
    the `protected_content_removed` property) after the stream completes.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._system_prompt_depth = 0
        self._protected_content_removed = False
        self._finished = False

    @property
    def protected_content_removed(self) -> bool:
        return self._protected_content_removed

    def feed(self, chunk: str) -> SanitizedPersonaOutput:
        if self._finished:
            raise RuntimeError("persona output stream filter is already finished")
        if not isinstance(chunk, str):
            raise TypeError("persona output chunks must be strings")

        self._pending += chunk
        emitted: list[str] = []

        while self._pending:
            if self._system_prompt_depth:
                folded = self._pending.casefold()
                matches = [
                    (index, marker) for marker in _OUTSIDE_MARKERS if (index := folded.find(marker.casefold())) >= 0
                ]
                if matches:
                    marker_at, marker = min(matches, key=lambda item: item[0])
                    self._protected_content_removed = True
                    self._pending = self._pending[marker_at + len(marker) :]
                    if marker == _OPEN_SYSTEM_PROMPT:
                        self._system_prompt_depth += 1
                    else:
                        self._system_prompt_depth -= 1
                    continue

                keep = _longest_marker_prefix_suffix(self._pending, _OUTSIDE_MARKERS)
                if len(self._pending) > keep:
                    self._protected_content_removed = True
                self._pending = self._pending[-keep:] if keep else ""
                break

            folded = self._pending.casefold()
            matches = [(index, marker) for marker in _OUTSIDE_MARKERS if (index := folded.find(marker.casefold())) >= 0]
            if matches:
                marker_at, marker = min(matches, key=lambda item: item[0])
                emitted.append(self._pending[:marker_at])
                self._pending = self._pending[marker_at + len(marker) :]
                self._protected_content_removed = True
                if marker == _OPEN_SYSTEM_PROMPT:
                    self._system_prompt_depth = 1
                continue

            keep = _longest_marker_prefix_suffix(self._pending, _OUTSIDE_MARKERS)
            safe_length = len(self._pending) - keep
            if safe_length:
                emitted.append(self._pending[:safe_length])
            self._pending = self._pending[safe_length:]
            break

        return SanitizedPersonaOutput(
            text="".join(emitted),
            protected_content_removed=self._protected_content_removed,
        )

    def finish(self) -> SanitizedPersonaOutput:
        if self._finished:
            return SanitizedPersonaOutput("", self._protected_content_removed)

        emitted = ""
        if self._system_prompt_depth:
            if self._pending:
                self._protected_content_removed = True
        else:
            emitted = self._pending

        self._pending = ""
        self._finished = True
        return SanitizedPersonaOutput(emitted, self._protected_content_removed)


def sanitize_persona_output(text: str) -> SanitizedPersonaOutput:
    """Remove protected prompt envelopes from one complete persona response."""

    stream_filter = PersonaOutputStreamFilter()
    streamed = stream_filter.feed(text)
    finished = stream_filter.finish()
    return SanitizedPersonaOutput(
        text=streamed.text + finished.text,
        protected_content_removed=finished.protected_content_removed,
    )


def safe_persona_output_text(text: str) -> str:
    """Return legacy-safe persona text, with a useful fallback if all text was protected."""

    result = sanitize_persona_output(text)
    if result.protected_content_removed and not result.text.strip():
        return PERSONA_OUTPUT_REMOVED_FALLBACK
    return result.text
