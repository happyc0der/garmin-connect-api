"""Planned workouts and the training calendar."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Query

from ...models.workouts import ScheduledWorkout, StrengthWorkoutSpec, WorkoutSummary
from ...services import workouts as service

router = APIRouter(prefix="/workouts", tags=["workouts"])


@router.get("", operation_id="list_workouts", response_model=list[WorkoutSummary])
def list_workouts(
    limit: int = Query(100, ge=1, le=500), start: int = Query(0, ge=0)
) -> list[WorkoutSummary]:
    """List saved workouts."""
    return service.list_workouts(start=start, limit=limit)


@router.get(
    "/scheduled/{year}/{month}",
    operation_id="list_scheduled_workouts",
    response_model=list[ScheduledWorkout],
)
def scheduled(year: int, month: int) -> list[ScheduledWorkout]:
    """Calendar entries for one month. Month is 1 to 12."""
    return service.scheduled(year, month)


@router.get("/{workout_id}", operation_id="get_workout", response_model=WorkoutSummary)
def get_workout(workout_id: int) -> WorkoutSummary:
    """One saved workout, including its full step structure."""
    return service.get_workout(workout_id)


@router.post("/strength", operation_id="create_strength_workout", response_model=WorkoutSummary)
def create_strength(spec: StrengthWorkoutSpec) -> WorkoutSummary:
    """Create a strength workout to follow later.

    Each exercise becomes a block of sets with a rest between them. Name
    exercises with Garmin's category and exercise name, which the exercise
    search endpoint can find; an unknown name is rejected with suggestions.

    Give schedule_date to put it on the calendar in the same call.
    """
    return service.create_strength(spec)


@router.post("/raw", operation_id="create_workout_from_dto", response_model=WorkoutSummary)
def create_raw(
    workout: dict[str, Any] = Body(description="A complete Garmin workout DTO."),
) -> WorkoutSummary:
    """Create a workout from a full Garmin DTO, for shapes the builder omits."""
    return service.create_raw(workout)


@router.post(
    "/{workout_id}/schedule", operation_id="schedule_workout", response_model=ScheduledWorkout
)
def schedule(
    workout_id: int, day: date = Query(description="The date to schedule it on.")
) -> ScheduledWorkout:
    """Put a saved workout on the calendar for a date.

    Returns the calendar entry. Its schedule_id, not the workout id, is what
    unschedule_workout takes.
    """
    return service.schedule(workout_id, day)


@router.delete("/schedule/{schedule_id}", operation_id="unschedule_workout")
def unschedule(schedule_id: int) -> dict[str, Any]:
    """Remove a calendar entry. Takes the schedule id, not the workout id."""
    service.unschedule(schedule_id)
    return {"schedule_id": schedule_id, "status": "unscheduled"}


@router.post("/{workout_id}/push", operation_id="push_workout_to_device")
def push(workout_id: int, device_id: int | None = Query(None)) -> dict[str, Any]:
    """Send a workout to a watch.

    It arrives at the watch's next sync, not immediately. With no device id
    Garmin picks the primary training device.
    """
    service.push_to_device(workout_id, device_id)
    return {"workout_id": workout_id, "status": "queued for device"}


@router.delete("/{workout_id}", operation_id="delete_workout")
def delete(workout_id: int) -> dict[str, Any]:
    """Delete a saved workout."""
    service.delete(workout_id)
    return {"workout_id": workout_id, "status": "deleted"}


@router.get("/devices/list", operation_id="list_devices")
def devices() -> Any:
    """Garmin devices on the account, with the ids push_workout_to_device takes."""
    return service.devices()
