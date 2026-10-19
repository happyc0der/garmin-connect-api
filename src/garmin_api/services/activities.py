"""Reading and editing activities."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date
from typing import Any, Literal

from .. import auth
from ..errors import InvalidRequest, NotFound
from ..models.activities import Activity, ActivitySets, ActivitySummary, SetEdit
from ..models.common import as_date_str
from ..models.laps import ActivityLaps

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

    With a date range this asks Garmin's search endpoint for one page of
    ``limit`` rows; ``end_date`` needs ``start_date``. ``activity_type`` takes
    Garmin's own keys, e.g. ``strength_training``, ``running``, ``cycling``.
    """
    if end_date and not start_date:
        raise InvalidRequest(
            "end_date needs a start_date.",
            detail="Garmin's date search takes a range; give both, or neither for the latest.",
        )
    parent, subtype = resolve_type(activity_type)
    if start_date:
        # One page of Garmin's search endpoint, bounded by ``limit``. The
        # library's own date-range method pages until Garmin runs dry -- the
        # whole history for an early start date -- before anything is cut.
        params: dict[str, str] = {
            "startDate": as_date_str(start_date),
            "start": str(start),
            "limit": str(limit),
        }
        if end_date:
            params["endDate"] = as_date_str(end_date)
        if parent:
            params["activityType"] = parent
        if subtype:
            params["activitySubType"] = subtype
        rows = list(auth.call("connectapi", _activity_search_url(), params=params) or [])
        if subtype:
            # Belt and braces, in case the endpoint ignores the sub-type.
            rows = [r for r in rows if (r.get("activityType") or {}).get("typeKey") == subtype]
    else:
        rows = list(auth.call("get_activities", start, limit, parent, subtype) or [])
    return [
        ActivitySummary.from_garmin(r, include_raw=include_raw) for r in rows if isinstance(r, dict)
    ]


def _activity_search_url() -> str:
    """The search endpoint's path, as the library defines it.

    Read from an instance that has not logged in -- constructing one makes no
    request -- so the path stays the library's, not a copy of it.
    """
    global _search_url
    if _search_url is None:
        from garminconnect import Garmin

        _search_url = str(Garmin().garmin_connect_activities)
    return _search_url


_search_url: str | None = None


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
    if not isinstance(payload, dict):
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


def laps(activity_id: int | str) -> ActivityLaps:
    """Laps for a run or ride, and lengths in 100s for a pool swim. Two requests."""
    from ..models.laps import build

    detail = auth.call("get_activity", str(activity_id)) or {}
    if not detail:
        raise NotFound(f"No activity {activity_id}.")
    summary = detail.get("summaryDTO") or detail
    sport = ((detail.get("activityTypeDTO") or detail.get("activityType")) or {}).get("typeKey")
    pool_length, pool_unit = summary.get("poolLength"), None
    unit = (summary.get("unitOfPoolLength") or {}).get("unitKey")
    if pool_length:
        pool_unit = "yd" if unit == "yard" else "m"
        # Garmin states the length in the pool's own unit; keep it there.
        pool_length = float(pool_length)
    splits = auth.call("get_activity_splits", str(activity_id)) or {}
    return build(int(activity_id), sport, splits, pool_length=pool_length, pool_unit=pool_unit)


# Garmin replaces an activity's sets whole, so two edits running at once would
# each write back the list they read, and the first one's change would vanish.
_edit_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_edit_locks_guard = threading.Lock()


def edit_sets(activity_id: int | str, edit: SetEdit) -> ActivitySets:
    """Correct an activity's sets; see :func:`_edit_sets`. One edit per activity at a time."""
    with _edit_locks_guard:
        lock = _edit_locks[str(activity_id)]
    with lock:
        return _edit_sets(activity_id, edit)


def _edit_sets(activity_id: int | str, edit: SetEdit) -> ActivitySets:
    """Correct a strength activity's sets, and return them as Garmin now has them.

    Watches guess the exercise from wrist movement and often guess wrong.
    Garmin replaces all of an activity's sets at once, so this reads them,
    applies only the requested changes, and writes the full list back. Every
    exercise is checked against the catalogue first.
    """
    from .. import catalogue
    from ..errors import InvalidRequest

    if not edit.changes and not edit.delete:
        raise InvalidRequest("Nothing to change.", detail="Give changes, sets to delete, or both.")

    payload = auth.call("get_activity_exercise_sets", activity_id)
    rows = list((payload or {}).get("exerciseSets") or [])
    if not rows:
        raise NotFound(f"Activity {activity_id} has no sets to edit.")

    # Working set number -> row position, numbered as get_sets numbers them.
    positions: dict[int, int] = {}
    for position, row in enumerate(rows):
        if str(row.get("setType") or "").upper() != "REST":
            positions[len(positions) + 1] = position

    named = {n for change in edit.changes for n in change.sets} | set(edit.delete)
    unknown = sorted(n for n in named if n not in positions)
    if unknown:
        raise InvalidRequest(
            f"No working set numbered {unknown}.",
            detail=f"This activity has sets 1 to {len(positions)}.",
        )
    if set(edit.delete) & {n for change in edit.changes for n in change.sets}:
        raise InvalidRequest("A set cannot be both changed and deleted.")

    for change in edit.changes:
        exercise = None
        if change.category is not None:
            name = (change.exercise_name or "").upper().strip()
            category = change.category.upper().strip()
            if not name or name == category:
                # Garmin accepts a known category with no exercise name.
                if category not in catalogue.categories():
                    catalogue.validate(category, "")  # raises, with suggestions
                name = ""
            else:
                catalogue.validate(category, name)
            exercise = {"category": category, "name": name or None, "probability": 100.0}
        for number in change.sets:
            row = rows[positions[number]]
            if exercise is not None:
                row["exercises"] = [dict(exercise)]
            if change.reps is not None:
                row["repetitionCount"] = change.reps
            if change.weight_kg is not None:
                row["weight"] = round(change.weight_kg * 1000.0, 1)

    drop = {positions[n] for n in edit.delete}
    kept = _without_stranded_rests(rows, drop)
    auth.call(
        "set_activity_exercise_sets",
        activity_id,
        {"activityId": int(activity_id), "exerciseSets": kept},
    )
    return get_sets(activity_id)


def _is_rest(row: dict[str, Any]) -> bool:
    return str(row.get("setType") or "").upper() == "REST"


def _without_stranded_rests(rows: list[dict[str, Any]], drop: set[int]) -> list[dict[str, Any]]:
    """The rows left after deleting ``drop``, without rests the deletion stranded.

    Deleting a set leaves the rests on either side of it: two rests in a row,
    or one at the end with no set after it. Only rests next to a deleted set
    are touched; any others are as the watch recorded them.
    """
    near = {p + step for p in drop for step in (-1, 1)}
    kept: list[tuple[int, dict[str, Any]]] = []
    for position, row in enumerate(rows):
        if position in drop:
            continue
        if _is_rest(row) and position in near and (not kept or _is_rest(kept[-1][1])):
            continue  # a rest after a rest, or at the start
        kept.append((position, row))
    while kept and _is_rest(kept[-1][1]) and kept[-1][0] in near:
        kept.pop()  # a rest with no set after it
    return [row for _, row in kept]
