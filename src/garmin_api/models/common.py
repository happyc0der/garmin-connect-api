"""Shared helpers for turning Garmin's JSON into typed values."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    """Base for every model here.

    ``populate_by_name`` lets callers build one with either the field name or
    Garmin's own key, which keeps round-tripping raw payloads painless.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class WriteSpec(ApiModel):
    """Base for what a caller sends to be written to Garmin.

    Unknown keys are refused rather than ignored: a misspelt or misplaced
    field would otherwise vanish and the write go ahead without it, such as
    a set saved as plain "Row" because the exercise came under another key.
    """

    # No infinities or NaN: JSON can carry them as 1e999, and a step a
    # million miles long or an infinite speed is never meant.
    model_config = ConfigDict(populate_by_name=True, extra="forbid", allow_inf_nan=False)


# FIT counts time from 31 Dec 1989 in an unsigned 32-bit field, so a time
# outside these years cannot be written to a file.
EARLIEST = datetime(1990, 1, 1, tzinfo=UTC)
LATEST = datetime(2100, 1, 1, tzinfo=UTC)


def within_fit_years(value: datetime) -> datetime:
    """``value`` if FIT can hold it; a local time without an offset is judged by its year."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not EARLIEST <= aware < LATEST:
        raise ValueError("must fall between 1990 and 2099")
    return value


# The exercise search returns the variant as "exercise"; the write specs call
# it "exercise_name". Either is accepted, so a search result can be passed on.
EXERCISE_NAME_KEYS = ("exercise_name", "exercise")


def _search_result_keys(schema: dict[str, Any]) -> None:
    """Declare the search result's own keys, which strict clients check against."""
    properties = schema.setdefault("properties", {})
    properties.setdefault(
        "exercise",
        {"type": "string", "description": "The same as exercise_name: find_exercise's key."},
    )
    properties.setdefault(
        "name",
        {"type": "string", "description": "find_exercise's display name. Ignored."},
    )


class ExerciseSpec(WriteSpec):
    """A write spec naming an exercise, which takes a find_exercise result as it is."""

    model_config = ConfigDict(json_schema_extra=_search_result_keys)

    @model_validator(mode="before")
    @classmethod
    def _search_result(cls, data: Any) -> Any:
        """Take the display name a find_exercise result carries.

        Beside the variant it adds nothing and is dropped. Alone, it is the
        only thing saying which variant is meant, so it is looked up; it must
        never be dropped then, or the generic exercise would be saved.
        """
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            return data
        data = dict(data)
        name = data.pop("name")
        if any(data.get(key) for key in EXERCISE_NAME_KEYS):
            return data
        from .. import catalogue

        found = catalogue.resolve(name)
        category = str(data.get("category") or "").upper().strip()
        if found is None or (category and found.category != category):
            raise ValueError(
                f"name {name!r} is not an exercise in category {category or '?'}; "
                "give exercise_name (find_exercise's 'exercise')"
            )
        data.setdefault("category", found.category)
        data["exercise_name"] = found.exercise
        return data


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
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_local(value: Any) -> str | None:
    """A wall-clock time, as ``YYYY-MM-DDTHH:MM:SS`` with no offset.

    Garmin's ``*Local`` fields are the time on the watch where the activity
    happened, with no zone given. Tagging one as UTC would state a falsehood
    -- 19:09 in New York is not 19:09 UTC -- and a datetime without an offset
    is not a valid JSON Schema ``date-time``, which strict MCP clients reject.
    So it travels as what it is: a plain local timestamp string.
    """
    parsed = parse_dt(value)
    return parsed.replace(tzinfo=None).isoformat(timespec="seconds") if parsed else None


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
