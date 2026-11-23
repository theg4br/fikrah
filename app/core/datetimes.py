"""Timestamp compatibility for aware PostgreSQL and naive SQLite values."""

from datetime import datetime, timezone


def ensure_timezone(value: datetime) -> datetime:
    """Interpret naive persisted values as UTC; preserve existing aware values."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
