"""Activity routes."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Response

from ...models.activities import Activity, ActivitySets, ActivitySummary
from ...services import activities as service

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", operation_id="list_activities", response_model=list[ActivitySummary])
def list_activities(
    limit: int = Query(20, ge=1, le=200),
    start: int = Query(0, ge=0),
    activity_type: str | None = Query(
        None, description='Garmin type key, e.g. "strength_training", "running", "cycling".'
    ),
    start_date: date | None = Query(None, description="Earliest date, inclusive."),
    end_date: date | None = Query(None, description="Latest date, inclusive."),
) -> list[ActivitySummary]:
    """List activities, newest first.

    Filter by type and by date range. For strength workouts pass
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
def get_activity(activity_id: int) -> Activity:
    """One activity's summary: type, timing, distance, heart rate, calories."""
    return service.get_activity(activity_id)


@router.get("/{activity_id}/sets", operation_id="get_activity_sets", response_model=ActivitySets)
def get_sets(
    activity_id: int,
    include_rests: bool = Query(False, description="Include the rest periods between sets."),
) -> ActivitySets:
    """Per-set detail for a strength activity: exercise, reps, weight, duration.

    Only strength activities have these. Rests are excluded by default.
    """
    return service.get_sets(activity_id, include_rests=include_rests)


@router.get("/{activity_id}/splits", operation_id="get_activity_splits")
def get_splits(activity_id: int) -> Any:
    """Lap and split detail."""
    return service.get_splits(activity_id)


@router.get("/{activity_id}/file", operation_id="download_activity_file")
def download(
    activity_id: int,
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
    activity_id: int,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Rename an activity or change its description."""
    changed: dict[str, Any] = {}
    if name is not None:
        service.set_name(activity_id, name)
        changed["name"] = name
    if description is not None:
        service.set_description(activity_id, description)
        changed["description"] = description
    return {"activity_id": activity_id, "updated": changed}


@router.delete("/{activity_id}", operation_id="delete_activity")
def delete(activity_id: int) -> dict[str, Any]:
    """Permanently delete an activity. Garmin Connect offers no undo."""
    service.delete(activity_id)
    return {"activity_id": activity_id, "status": "deleted"}
