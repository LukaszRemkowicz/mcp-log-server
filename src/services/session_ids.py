"""Human-readable session id generation."""

from __future__ import annotations

from faker import Faker

_fake = Faker()
SESSION_ID_MAX_LENGTH = 24


def generate_session_id() -> str:
    """Return one readable random session id."""

    for _ in range(20):
        words = [word.lower() for word in _fake.words(nb=3)]
        session_id = "-".join([*words, _fake.hexify(text="^^^^").lower()])
        if len(session_id) <= SESSION_ID_MAX_LENGTH:
            return session_id

    words = [word.lower()[:5] for word in _fake.words(nb=3)]
    fallback = "-".join([*words, _fake.hexify(text="^^^^").lower()])
    return fallback[:SESSION_ID_MAX_LENGTH].rstrip("-")
