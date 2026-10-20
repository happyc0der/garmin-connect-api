"""Activity routes."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Path, Query, Response

from ...errors import InvalidRequest
from ...models.activities import Activity, ActivitySets, ActivitySummary, SetEdit
from ...models.laps import ActivityLaps
from ...services import activities as service

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", operation_id="list_activities", response_model=list[ActivitySummary])
def list_activities(
    limit: int = Query(20, ge=1, le=200, description="Most activities to return."),
    start: int = Query(
        0, ge=0, description="Activities to skip, for paging. An offset, not a date."
    ),
    activity_type: str | None = Query(
        None, description='Garmin type key, e.g. "strength_training", "running", "cycling".'
    ),
    start_date: date | None = Query(None, description="Earliest date, inclusive."),
    end_date: date | None = Query(None, description="Latest date, inclusive."),
) -> list[ActivitySummary]:
    """List activities, newest first.

    Completed activities, not planned workouts. Filter by type and by date
    range; end_date needs start_date. For strength sessions pass
    activity_type=strength_training, then fetch the sets of one to see the
    exercises, reps and weights.
    """
    return service.list_activities(
        start=start,
        limit=limit,
        activity_type=activity_type,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/last", operation_id="get_last_activity", response_model=ActivitySummary | None)
def last_activity() -> ActivitySummary | None:
    """The single most recent activity."""
    return service.last_activity()


@router.get("/{activity_id}", operation_id="get_activity", response_model=Activity)
def get_activity(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
) -> Activity:
    """Return one activity by id: type, start time (UTC and local wall clock),
    duration, distance, heart rate and calories, plus Garmin's full record under raw.

    Ids come from list_activities, recent_strength_sessions or week_summary. It
    has no per-set or per-lap detail: for a strength session's exercises, reps and
    weights use get_activity_sets; for a run, ride or swim's laps and pacing,
    get_activity_laps.
    """
    return service.get_activity(activity_id)


@router.get("/{activity_id}/sets", operation_id="get_activity_sets", response_model=ActivitySets)
def get_sets(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
    include_rests: bool = Query(False, description="Include the rest periods between sets."),
) -> ActivitySets:
    """Return a strength activity's sets: exercise category and name, reps, weight in
    kg and duration, with the total volume.

    Each working set has a set_number (1, 2, 3...), which edit_activity_sets takes
    to correct or delete sets; rests have none and are left out unless
    include_rests. Only strength activities have sets: for runs, rides and swims
    use get_activity_laps. For the last few sessions at once,
    recent_strength_sessions saves a call per session.
    """
    return service.get_sets(activity_id, include_rests=include_rests)


@router.get("/{activity_id}/laps", operation_id="get_activity_laps", response_model=ActivityLaps)
def get_laps(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
) -> ActivityLaps:
    """Return the laps of a run or ride, or the pace per 100 m or yd of a pool swim,
    with a summary: average pace or speed, first half against second half, and the
    fastest and slowest lap or 100.

    Answers "did my pace hold up?". Strength sessions have sets, not laps: use
    get_activity_sets. Two Garmin requests.
    """
    return service.laps(activity_id)


@router.patch("/{activity_id}/sets", operation_id="edit_activity_sets", response_model=ActivitySets)
def edit_sets(
    edit: SetEdit,
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
) -> ActivitySets:
    """Correct a strength activity's sets: exercise, reps or weight, or delete sets.

    Address sets by the set_number get_activity_sets shows; within one call,
    every number means the set as numbered before it. Deleting renumbers the
    sets after it, so repeating a delete removes a different set: use the
    numbers this returns for any further edit. Name exercises with Garmin's
    category and exercise_name; find_exercise looks them up. To rename the
    activity or change its description, use update_activity.

    Returns the sets as Garmin now holds them.
    """
    return service.edit_sets(activity_id, edit)


@router.get("/{activity_id}/splits", operation_id="get_activity_splits")
def get_splits(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
) -> Any:
    """Lap and split detail."""
    return service.get_splits(activity_id)


@router.get("/{activity_id}/file", operation_id="download_activity_file")
def download(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
    fmt: str = Query("fit", pattern="^(fit|tcx|gpx|csv)$"),
) -> Response:
    """Download the activity's file.

    The fit format returns Garmin's original, which for a strength activity
    carries the set messages. It arrives as a zip archive.
    """
    data = service.download(activity_id, fmt)  # type: ignore[arg-type]
    media = {
        "fit": "application/zip",
        "tcx": "application/vnd.garmin.tcx+xml",
        "gpx": "application/gpx+xml",
        "csv": "text/csv",
    }[fmt]
    suffix = "zip" if fmt == "fit" else fmt
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="activity_{activity_id}.{suffix}"'},
    )


@router.patch("/{activity_id}", operation_id="update_activity")
def update(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
    name: str | None = Query(None, description="The new title."),
    description: str | None = Query(None, description="The new description, replacing the old."),
) -> dict[str, Any]:
    """Rename an activity, replace its description, or both; what is not given is left as it is.

    For a strength session's exercises, reps or weights use edit_activity_sets; to
    remove the activity, delete_activity. The new text replaces the old. Name and
    description are separate Garmin requests, so if the second fails the first
    still stands.
    """
    if name is None and description is None:
        raise InvalidRequest("Nothing to update.", detail="Give a name, a description, or both.")
    changed: dict[str, Any] = {}
    if name is not None:
        service.set_name(activity_id, name)
        changed["name"] = name
    if description is not None:
        service.set_description(activity_id, description)
        changed["description"] = description
    return {"activity_id": activity_id, "updated": changed}


@router.delete("/{activity_id}", operation_id="delete_activity")
def delete(
    activity_id: int = Path(gt=0, description="Garmin's activity id, from list_activities."),
) -> dict[str, Any]:
    """Permanently delete one activity from Garmin Connect. There is no undo.

    Use it for an activity that should not exist, such as a session logged twice.
    To fix a strength session's sets use edit_activity_sets, and to rename it,
    update_activity; both keep the activity. A copy already shared to Strava or
    another connected app stays there.
    """
    service.delete(activity_id)
    return {"activity_id": activity_id, "status": "deleted"}
