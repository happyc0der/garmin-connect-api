"""Shared helpers for turning Garmin's JSON into typed values."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for every model here.

    ``populate_by_name`` lets callers build one with either the field name or
    Garmin's own key, which keeps round-tripping raw payloads painless.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Paging(BaseModel):
    start: int = 0
    limit: int = 20


def parse_dt(value: Any) -> datetime | None:
    """Parse the several shapes Garmin uses for an instant.

    Seen in the wild: epoch milliseconds as an int, ISO 8601 with a Z,
    and "2026-09-21 07:14:33.0" with no zone at all (which is GMT in the
    ``*GMT`` fields and local time in the others -- the caller picks the field,
    this just parses what it is given and assumes UTC when nothing says).
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        # Garmin sends milliseconds for activity timestamps.
        seconds = value / 1000 if value > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_date(value: Any) -> date | None:
    dt = parse_dt(value)
    return dt.date() if dt else None


def as_date_str(value: date | datetime | str) -> str:
    """Garmin's date parameters are all ``YYYY-MM-DD`` strings."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def first(payload: dict[str, Any], *keys: str) -> Any:
    """The first present, non-null value among ``keys``.

    Garmin renames fields between endpoints for the same quantity, so most
    mappings need to try two or three spellings.
    """
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def grams_to_kg(value: Any) -> float | None:
    """Garmin carries strength weights in grams almost everywhere."""
    if value is None:
        return None
    try:
        return round(float(value) / 1000.0, 3)
    except (TypeError, ValueError):
        return None


def raw_field() -> Any:
    return Field(default=None, description="Garmin's unmapped original payload.")
