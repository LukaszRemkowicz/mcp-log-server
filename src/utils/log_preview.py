"""Pure preview-shaping helpers for agent-facing log responses.

These helpers do not perform collection, persistence, or authorization. They
only apply deterministic response-size policy to already collected log text.
That makes `utils/` a better home than `services/`, because there is no
service-style orchestration or state involved.
"""

from __future__ import annotations

MAX_INLINE_LOG_BYTES = 200_000


def truncate_log_preview(content: str, max_bytes: int = MAX_INLINE_LOG_BYTES) -> str:
    """Return a byte-bounded UTF-8 preview for one collected log body."""

    encoded_content = content.encode("utf-8")
    if len(encoded_content) <= max_bytes:
        return content
    return encoded_content[:max_bytes].decode("utf-8", errors="ignore")
