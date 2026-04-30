"""Pure preview-shaping helpers for agent-facing log responses.

These helpers do not perform collection, persistence, or authorization. They
only apply deterministic response-size policy to already collected log text.
That makes `utils/` a better home than `services/`, because there is no
service-style orchestration or state involved.
"""

from __future__ import annotations

from tools.models import CollectedSourcePayload

MAX_INLINE_LOG_BYTES = 200_000


def truncate_log_preview(content: str, max_bytes: int = MAX_INLINE_LOG_BYTES) -> str:
    """Return a byte-bounded UTF-8 preview for one collected log body."""

    encoded_content = content.encode("utf-8")
    if len(encoded_content) <= max_bytes:
        return content
    return encoded_content[:max_bytes].decode("utf-8", errors="ignore")


def truncate_collected_sources_for_response(
    collected_sources: list[CollectedSourcePayload],
) -> bool:
    """Replace large source content with a bounded preview for the response body."""

    any_response_truncated = False
    for source in collected_sources:
        if source.status != "collected":
            continue

        preview_content = truncate_log_preview(source.content)
        source.content_truncated = preview_content != source.content
        source.content = preview_content
        if source.content_truncated:
            any_response_truncated = True

    return any_response_truncated
