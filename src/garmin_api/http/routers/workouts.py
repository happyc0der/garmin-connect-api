"""Planned workouts and the training calendar."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Path, Query

from ...models.health import DeviceSummary
from ...models.workouts import (
    EnduranceWorkoutSpec,
    ScheduledWorkout,
    StrengthWorkoutSpec,
    WorkoutSummary,
)
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
def scheduled(
    year: int = Path(ge=2000, le=2100), month: int = Path(ge=1, le=12)
) -> list[ScheduledWorkout]:
    """Calendar entries for one month. Month is 1 to 12."""
    return service.scheduled(year, month)


@router.get("/{workout_id}", operation_id="get_workout", response_model=WorkoutSummary)
def get_workout(workout_id: int = Path(gt=0)) -> WorkoutSummary:
    """One saved workout, including its full step structure."""
    return service.get_workout(workout_id)


@router.post("/strength", operation_id="create_strength_workout", response_model=WorkoutSummary)
def create_strength(spec: StrengthWorkoutSpec) -> WorkoutSummary:
    """Create a strength workout to follow later.

    Each exercise becomes a block of sets with a rest between them. Name
    exercises with Garmin's category and exercise name, which the exercise
    search endpoint can find; an unknown name is rejected with suggestions.

    Without schedule_date it is saved, not scheduled. With it, it is put on
    the calendar in the same call, and the reply carries the schedule_id.
    """
    return service.create_strength(spec)


@router.post("/endurance", operation_id="create_endurance_workout", response_model=WorkoutSummary)
def create_endurance(spec: EnduranceWorkoutSpec) -> WorkoutSummary:
    """Create a run, ride or pool swim workout to follow on the watch.

    Steps end on a time, a distance or the lap button, and can repeat. Runs
    and rides take a target: pace per km or mile, speed, heart-rate zone or
    range, power zone or watts, or cadence. Swims take a stroke, equipment and
    a pool length; distances must be whole lengths. Give schedule_date to put
    it on the calendar too; the reply then carries the schedule_id.
    """
    return service.create_endurance(spec)


@router.post("/raw", operation_id="create_workout_from_dto", response_model=WorkoutSummary)
def create_raw(
    workout: dict[str, Any] = Body(description="A complete Garmin workout DTO."),
) -> WorkoutSummary:
    """Create a workout from a full Garmin DTO, for shapes the builder omits.

    Sent to Garmin as given: exercise names are not checked against the
    catalogue here, so prefer create_strength_workout where it can express it.
    """
    return service.create_raw(workout)


@router.post(
    "/{workout_id}/schedule", operation_id="schedule_workout", response_model=ScheduledWorkout
)
def schedule(
    workout_id: int = Path(gt=0), day: date = Query(description="The date to schedule it on.")
) -> ScheduledWorkout:
    """Put a saved workout on the calendar for a date.

    Returns the calendar entry. Its schedule_id, not the workout id, is what
    unschedule_workout takes. Scheduling again adds a second entry; to move a
    workout to another day, unschedule the old entry first.
    """
    return service.schedule(workout_id, day)


@router.delete("/schedule/{schedule_id}", operation_id="unschedule_workout")
def unschedule(schedule_id: int = Path(gt=0)) -> dict[str, Any]:
    """Remove a calendar entry. Takes the schedule id, not the workout id."""
    service.unschedule(schedule_id)
    return {"schedule_id": schedule_id, "status": "unscheduled"}


@router.post("/{workout_id}/push", operation_id="push_workout_to_device")
def push(workout_id: int = Path(gt=0), device_id: int | None = Query(None)) -> dict[str, Any]:
    """Send a workout to a watch.

    It arrives at the watch's next sync, not immediately. With no device id
    the last-used device is chosen if it is a real device on the account,
    else the primary activity tracker; the reply names the device used.
    """
    target = service.push_to_device(workout_id, device_id)
    return {
        "workout_id": workout_id,
        "device_id": target.device_id,
        "device_name": target.name or target.product,
        "status": "queued for device",
    }


@router.delete("/{workout_id}", operation_id="delete_workout")
def delete(workout_id: int = Path(gt=0)) -> dict[str, Any]:
    """Delete a saved workout."""
    service.delete(workout_id)
    return {"workout_id": workout_id, "status": "deleted"}


@router.get("/devices/list", operation_id="list_devices", response_model=list[DeviceSummary])
def devices() -> list[DeviceSummary]:
    """Garmin devices on the account, with the ids push_workout_to_device takes."""
    return service.devices()
