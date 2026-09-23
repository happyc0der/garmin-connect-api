"""Reading and editing activities."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from .. import auth
from ..errors import NotFound
from ..models.activities import Activity, ActivitySets, ActivitySummary
from ..models.common import as_date_str

DownloadFormat = Literal["fit", "tcx", "gpx", "csv"]

STRENGTH = "strength_training"


def list_activities(
    *,
    start: int = 0,
    limit: int = 20,
    activity_type: str | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    include_raw: bool = False,
) -> list[ActivitySummary]:
    """List activities, newest first.

    With a date range Garmin uses a different endpoint that ignores paging, so
    ``limit`` is applied here instead. ``activity_type`` takes Garmin's own
    keys, e.g. ``strength_training``, ``running``, ``cycling``.
    """
    parent, subtype = resolve_type(activity_type)
    if start_date:
        # The date-range endpoint takes a top-level type only, so a sub-type is
        # narrowed here after fetching its parent.
        rows = auth.call(
            "get_activities_by_date",
            as_date_str(start_date),
            as_date_str(end_date) if end_date else None,
            parent,
        )
        rows = list(rows or [])
        if subtype:
            rows = [r for r in rows if (r.get("activityType") or {}).get("typeKey") == subtype]
        rows = rows[start : start + limit]
    else:
        rows = auth.call("get_activities", start, limit, parent, subtype)
        rows = list(rows or [])
    return [ActivitySummary.from_garmin(r, include_raw=include_raw) for r in rows]


# Garmin's type tree is rooted at "all" (id 17). Its children are the top-level
# types the list endpoints accept as a type; everything below them, including
# strength_training and lap_swimming, has to be sent as a sub-type of its parent.
ROOT_TYPE_ID = 17

_types_cache: list[dict[str, Any]] | None = None


def _types() -> list[dict[str, Any]]:
    global _types_cache
    if _types_cache is None:
        _types_cache = list(auth.call("get_activity_types") or [])
    return _types_cache


def resolve_type(type_key: str | None) -> tuple[str | None, str | None]:
    """Split a type key into the (type, sub-type) pair Garmin's list wants.

    ``running`` -> ``("running", None)``;
    ``strength_training`` -> ``("fitness_equipment", "strength_training")``.
    An unknown key is passed through unchanged so Garmin can say what is wrong.
    Costs one call per process, the first time a filter is used.
    """
    if not type_key:
        return None, None
    by_key = {t.get("typeKey"): t for t in _types()}
    entry = by_key.get(type_key)
    if entry is None or entry.get("parentTypeId") in (None, ROOT_TYPE_ID):
        return type_key, None
    parent = next((t for t in _types() if t.get("typeId") == entry["parentTypeId"]), None)
    if parent is None:
        return type_key, None
    return parent.get("typeKey"), type_key


def get_activity(activity_id: int | str, *, include_raw: bool = True) -> Activity:
    payload = auth.call("get_activity", str(activity_id))
    if not payload:
        raise NotFound(f"No activity {activity_id}.")
    return Activity.from_garmin(payload, include_raw=include_raw)


def get_sets(
    activity_id: int | str, *, include_rests: bool = False, include_raw: bool = False
) -> ActivitySets:
    """Per-set strength detail: exercise, reps, weight, duration.

    Garmin records the rests between sets as rows of their own. They are
    dropped unless asked for, since "how many sets did I do" should not count
    the gaps.
    """
    payload = auth.call("get_activity_exercise_sets", activity_id)
    if payload is None:
        raise NotFound(f"No exercise sets for activity {activity_id}.")
    parsed = ActivitySets.from_garmin(int(activity_id), payload, include_raw=include_raw)
    if not include_rests:
        parsed.sets = parsed.working_sets
    return parsed


def get_details(activity_id: int | str, *, max_chart: int = 2000, max_poly: int = 4000) -> Any:
    """The full sample-level detail. Large -- often hundreds of kilobytes."""
    return auth.call("get_activity_details", str(activity_id), max_chart, max_poly)


def get_splits(activity_id: int | str) -> Any:
    return auth.call("get_activity_splits", str(activity_id))


def download(activity_id: int | str, fmt: DownloadFormat = "fit") -> bytes:
    """Download the activity file.

    ``fit`` gives Garmin's original, which for a strength activity carries the
    set messages. Note it is a zip archive, not a bare FIT file.
    """
    from garminconnect import Garmin

    formats = {
        "fit": Garmin.ActivityDownloadFormat.ORIGINAL,
        "tcx": Garmin.ActivityDownloadFormat.TCX,
        "gpx": Garmin.ActivityDownloadFormat.GPX,
        "csv": Garmin.ActivityDownloadFormat.CSV,
    }
    return auth.call("download_activity", str(activity_id), formats[fmt])


def set_name(activity_id: int | str, name: str) -> Any:
    return auth.call("set_activity_name", str(activity_id), name)


def set_description(activity_id: int | str, description: str) -> Any:
    return auth.call("set_activity_description", str(activity_id), description)


def set_type(activity_id: int | str, type_id: int, type_key: str, parent_type_id: int) -> Any:
    """Change an activity's type.

    All four values are required and must agree; Garmin's own type list is
    available from :func:`activity_types`.
    """
    return auth.call("set_activity_type", str(activity_id), type_id, type_key, parent_type_id)


def set_sets(activity_id: int | str, payload: dict[str, Any]) -> Any:
    """Replace an activity's exercise sets wholesale.

    Garmin validates the category and name of every set, and replaces rather
    than merges, so send the complete list.
    """
    return auth.call("set_activity_exercise_sets", activity_id, payload)


def delete(activity_id: int | str) -> Any:
    """Permanently delete an activity. There is no undo in Garmin Connect."""
    return auth.call("delete_activity", str(activity_id))


def activity_types() -> Any:
    return auth.call("get_activity_types")


def last_activity(*, include_raw: bool = False) -> ActivitySummary | None:
    payload = auth.call("get_last_activity")
    return ActivitySummary.from_garmin(payload, include_raw=include_raw) if payload else None
