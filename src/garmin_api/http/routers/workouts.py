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
    limit: int = Query(100, ge=1, le=500, description="Most workouts to return."),
    start: int = Query(0, ge=0, description="Workouts to skip, for paging. An offset, not a date."),
) -> list[WorkoutSummary]:
    """List the saved workouts in the account's library: plans to follow, with name,
    sport and estimated duration but not their steps.

    A saved workout is not on the calendar until scheduled: for what is planned on
    which date use list_scheduled_workouts, and for sessions already done,
    list_activities. For one workout's full step structure, get_workout. Paged:
    start skips that many workouts.
    """
    return service.list_workouts(start=start, limit=limit)


@router.get(
    "/scheduled/{year}/{month}",
    operation_id="list_scheduled_workouts",
    response_model=list[ScheduledWorkout],
)
def scheduled(
    year: int = Path(ge=2000, le=2100, description="Calendar year, e.g. 2026."),
    month: int = Path(ge=1, le=12, description="1 for January to 12 for December."),
) -> list[ScheduledWorkout]:
    """List the workouts scheduled on the Garmin calendar in one month, with each
    entry's schedule_id, workout_id and date.

    Only planned workouts are returned: the completed activities, naps and badges
    Garmin's calendar also holds are dropped, as are the neighbouring months' days
    its grid spills into. For sessions done on those dates use list_activities;
    for the saved workouts whether scheduled or not, list_workouts. The schedule_id
    is what unschedule_workout takes.
    """
    return service.scheduled(year, month)


@router.get("/{workout_id}", operation_id="get_workout", response_model=WorkoutSummary)
def get_workout(
    workout_id: int = Path(gt=0, description="From list_workouts or list_scheduled_workouts."),
) -> WorkoutSummary:
    """Return one saved workout by id: name, sport, description and estimated
    duration, with Garmin's full definition (every step, target and repeat) under raw.

    Use it to see what a plan asks for before scheduling or pushing it, or to check
    what create_strength_workout or create_endurance_workout built. Ids come from
    list_workouts or list_scheduled_workouts. A completed session is an activity:
    read it with get_activity or get_activity_sets.
    """
    return service.get_workout(workout_id)


@router.post("/strength", operation_id="create_strength_workout", response_model=WorkoutSummary)
def create_strength(spec: StrengthWorkoutSpec) -> WorkoutSummary:
    """Create a strength workout to follow later.

    Each exercise becomes a block of sets with a rest between them. Name
    exercises with Garmin's category and exercise name, which find_exercise
    gives; an unknown name is rejected with suggestions. For runs, rides and
    swims use create_endurance_workout; to record a session already done,
    log_strength_session.

    Without schedule_date it is saved, not scheduled. With it, it is put on
    the calendar in the same call, and the reply carries the schedule_id. If
    scheduling fails the workout still exists and the error gives its id:
    schedule that one rather than creating it again.
    """
    return service.create_strength(spec)


@router.post("/endurance", operation_id="create_endurance_workout", response_model=WorkoutSummary)
def create_endurance(spec: EnduranceWorkoutSpec) -> WorkoutSummary:
    """Create a run, ride or pool swim workout to follow on the watch.

    Steps end on a time, a distance or the lap button, and can repeat. Runs
    and rides take a target: pace per km or mile, speed, heart-rate zone or
    range, power zone or watts, or cadence. Swims take a stroke, equipment and
    a pool length; distances must be whole lengths. Give schedule_date to put
    it on the calendar too; the reply then carries the schedule_id, and if
    scheduling fails the error gives the new workout's id. For strength
    workouts use create_strength_workout.
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
    workout_id: int = Path(gt=0, description="From list_workouts or list_scheduled_workouts."),
    day: date = Query(description="The date to schedule it on."),
) -> ScheduledWorkout:
    """Put a saved workout on the calendar for a date.

    Returns the calendar entry. Its schedule_id, not the workout id, is what
    unschedule_workout takes. Scheduling again adds a second entry; to move a
    workout to another day, unschedule the old entry first.
    """
    return service.schedule(workout_id, day)


@router.delete("/schedule/{schedule_id}", operation_id="unschedule_workout")
def unschedule(
    schedule_id: int = Path(gt=0, description="The calendar entry's id, not the workout's."),
) -> dict[str, Any]:
    """Remove one entry from the Garmin calendar; the saved workout itself is kept.

    Takes the schedule_id from list_scheduled_workouts, schedule_workout, or a
    create_* call made with schedule_date; not the workout_id. To move a workout,
    unschedule it and schedule_workout it on the new date. To delete the workout
    itself, use delete_workout.
    """
    service.unschedule(schedule_id)
    return {"schedule_id": schedule_id, "status": "unscheduled"}


@router.post("/{workout_id}/push", operation_id="push_workout_to_device")
def push(
    workout_id: int = Path(gt=0, description="From list_workouts or list_scheduled_workouts."),
    device_id: int | None = Query(
        None, description="From list_devices. Left out, chosen as described."
    ),
) -> dict[str, Any]:
    """Queue a saved workout for one of the account's watches; it arrives at the
    watch's next sync, not immediately.

    Without device_id it goes to the device last used, if it is on the account's
    list, else the primary activity tracker, else the only device; when none of
    those settles it, the call is refused and asks for a device_id. list_devices
    gives the ids, and the reply names the device chosen. Pushing does
    not schedule it: to put it on a date, use schedule_workout.
    """
    target = service.push_to_device(workout_id, device_id)
    return {
        "workout_id": workout_id,
        "device_id": target.device_id,
        "device_name": target.name or target.product,
        "status": "queued for device",
    }


@router.delete("/{workout_id}", operation_id="delete_workout")
def delete(
    workout_id: int = Path(gt=0, description="From list_workouts or list_scheduled_workouts."),
) -> dict[str, Any]:
    """Permanently delete a saved workout (a plan) from the account's workout library.

    To take a workout off one date but keep it, use unschedule_workout with the
    calendar entry's schedule_id instead. Completed sessions are activities, not
    workouts: delete those with delete_activity. Nothing here restores a deleted
    workout; get_workout first if its structure may be wanted again, since its
    raw field holds the full definition.
    """
    service.delete(workout_id)
    return {"workout_id": workout_id, "status": "deleted"}


@router.get("/devices/list", operation_id="list_devices", response_model=list[DeviceSummary])
def devices() -> list[DeviceSummary]:
    """List the Garmin devices paired with the account: device_id, name, product, and
    which one is the primary activity tracker.

    Use it to pick the device_id push_workout_to_device takes. A push without one
    prefers the device last used, which this does not show, so pass device_id
    when the device matters. One Garmin request.
    """
    return service.devices()
