"""Planned workouts: create, schedule, push to a watch.

A workout here is a plan, not a record of something done. For logging a
finished session see :mod:`garmin_api.services.uploads`.

The DTO Garmin's workout service wants is deep and fussy, so the typed
builders python-garminconnect ships are reused rather than reinvented. What
this layer adds is validation: every exercise is checked against the catalogue
first, so a typo comes back as a useful message instead of Garmin's bare 400.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from garminconnect.workout import (
    StrengthWorkout,
    WorkoutSegment,
    create_strength_set,
)

from .. import auth, catalogue
from ..errors import NotFound
from ..models.common import as_date_str
from ..models.workouts import ScheduledWorkout, StrengthWorkoutSpec, WorkoutSummary

SPORT_STRENGTH = {
    "sportTypeId": 5,
    "sportTypeKey": "strength_training",
    "displayOrder": 5,
}


def list_workouts(
    *, start: int = 0, limit: int = 100, include_raw: bool = False
) -> list[WorkoutSummary]:
    rows = auth.call("get_workouts", start, limit) or []
    return [WorkoutSummary.from_garmin(r, include_raw=include_raw) for r in rows]


def get_workout(workout_id: int | str, *, include_raw: bool = True) -> WorkoutSummary:
    payload = auth.call("get_workout_by_id", workout_id)
    if not payload:
        raise NotFound(f"No workout {workout_id}.")
    return WorkoutSummary.from_garmin(payload, include_raw=include_raw)


def build_strength(spec: StrengthWorkoutSpec) -> dict[str, Any]:
    """Turn a compact spec into Garmin's workout DTO, validating as it goes.

    Each exercise becomes one repeat group of "do the reps, then rest", which
    is the shape the Connect editor produces and the watch knows how to follow.
    Step orders must be unique across the whole segment, hence the counter.
    """
    steps = []
    order = 1
    estimated = 0
    for exercise in spec.exercises:
        catalogue.validate(exercise.category, exercise.exercise_name)
        steps.append(
            create_strength_set(
                category=exercise.category,
                step_order=order,
                sets=exercise.sets,
                reps=exercise.reps,
                rest_seconds=exercise.rest_s,
                exercise_name=exercise.exercise_name,
                weight_kg=exercise.weight_kg,
            )
        )
        # Three orders per block: the group, its exercise step, its rest step.
        order += 3
        # Rough but honest: about four seconds a rep, plus the rests.
        estimated += exercise.sets * (exercise.reps * 4 + exercise.rest_s)

    workout = StrengthWorkout(
        workoutName=spec.name,
        description=spec.description,
        estimatedDurationInSecs=int(estimated),
        workoutSegments=[
            WorkoutSegment(segmentOrder=1, sportType=SPORT_STRENGTH, workoutSteps=steps)
        ],
    )
    return workout.to_dict()


def create_strength(spec: StrengthWorkoutSpec) -> WorkoutSummary:
    """Create a strength workout, and schedule it if the spec says to."""
    payload = build_strength(spec)
    created = auth.call("upload_workout", payload)
    summary = WorkoutSummary.from_garmin(created or {}, include_raw=True)
    if spec.schedule_date and summary.workout_id:
        schedule(summary.workout_id, spec.schedule_date)
    return summary


def create_raw(workout_json: dict[str, Any]) -> WorkoutSummary:
    """Create a workout from a full Garmin DTO, for shapes the builder omits."""
    created = auth.call("upload_workout", workout_json)
    return WorkoutSummary.from_garmin(created or {}, include_raw=True)


def update(workout_id: int | str, workout_json: dict[str, Any]) -> Any:
    return auth.call("update_workout", workout_id, workout_json)


def delete(workout_id: int | str) -> Any:
    return auth.call("delete_workout", workout_id)


def schedule(workout_id: int | str, day: date | str) -> Any:
    """Put a workout on the calendar for a date."""
    return auth.call("schedule_workout", workout_id, as_date_str(day))


def unschedule(schedule_id: int | str) -> Any:
    """Remove a calendar entry. Takes the schedule id, not the workout id."""
    return auth.call("unschedule_workout", schedule_id)


def scheduled(year: int, month: int, *, include_raw: bool = False) -> list[ScheduledWorkout]:
    """Calendar entries for a month. ``month`` is 1-12."""
    payload = auth.call("get_scheduled_workouts", year, month) or {}
    rows = payload.get("calendarItems") or payload.get("workoutScheduleList") or []
    return [ScheduledWorkout.from_garmin(r, include_raw=include_raw) for r in rows]


def push_to_device(workout_id: int | str, device_id: int | str | None = None) -> Any:
    """Send a workout to a watch.

    The watch picks it up at its next sync, not instantly. With no device id
    Garmin chooses the primary training device.
    """
    return auth.call("push_workout_to_device", workout_id, device_id)


def devices() -> Any:
    return auth.call("get_devices")
