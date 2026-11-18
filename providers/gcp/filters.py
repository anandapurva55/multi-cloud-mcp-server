"""Helpers for building Cloud Logging / Cloud Build filter strings safely."""
from datetime import datetime, timedelta, timezone


def quote(value: str) -> str:
    """Return `value` as a double-quoted filter string literal, escaping backslashes and
    quotes so user/LLM-supplied text can't break out of the literal and inject clauses."""
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise ValueError("Control characters are not allowed in filter values.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rfc3339(minutes_ago: int, now: datetime = None) -> str:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
