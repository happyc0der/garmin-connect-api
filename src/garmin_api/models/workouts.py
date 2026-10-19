"""Workouts to plan, and completed sessions to log.

Two directions that are easy to confuse:

* a **workout** is a plan. It goes on the Garmin calendar and can be pushed to
  a watch to follow. :class:`StrengthWorkoutSpec` describes one.
* a **session** is something already done. It becomes an activity in the
  history, written as a FIT file. :class:`StrengthSessionSpec` describes one.

Both name exercises with Garmin's ``category`` / ``exercise_name`` pair, which
is validated against the catalogue before anything is sent.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field, field_validator

from .common import ApiModel, first, parse_date, parse_dt


class WorkoutSummary(ApiModel):
    workout_id: int
    name: str | None = None
    sport: str | None = None
    description: str | None = None
    estimated_duration_s: int | None = None
    updated: datetime | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_raw: bool = False) -> WorkoutSummary:
        sport = payload.get("sportType") or {}
        return cls(
            workout_id=int(first(payload, "workoutId", "workoutid") or 0),
            name=first(payload, "workoutName"),
            sport=sport.get("sportTypeKey") if isinstance(sport, dict) else None,
            description=first(payload, "description"),
            estimated_duration_s=first(payload, "estimatedDurationInSecs"),
            updated=parse_dt(first(payload, "updateDate", "createdDate")),
            raw=payload if include_raw else None,
        )


class ScheduledWorkout(ApiModel):
    schedule_id: int | None = Field(
        default=None, description="The calendar entry's id, which unschedule takes."
    )
    workout_id: int | None = None
    name: str | None = None
    sport: str | None = None
    day: date | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_raw: bool = False) -> ScheduledWorkout:
        workout = payload.get("workout") or {}
        return cls(
            schedule_id=first(payload, "workoutScheduleId", "id"),
            workout_id=first(workout, "workoutId") or first(payload, "workoutId"),
            name=first(workout, "workoutName") or first(payload, "title"),
            sport=first(payload, "sportTypeKey")
            or (workout.get("sportType") or {}).get("sportTypeKey"),
            day=parse_date(first(payload, "calendarDate", "date", "scheduleDate")),
            raw=payload if include_raw else None,
        )


class StrengthExerciseSpec(ApiModel):
    """One exercise block in a planned workout: N sets of M reps."""

    category: str = Field(description='Garmin exercise category, e.g. "BENCH_PRESS".')
    exercise_name: str = Field(
        default="",
        description='Variant, e.g. "BARBELL_BENCH_PRESS". Empty shows the category name alone.',
    )
    sets: int = Field(default=1, ge=1, le=99)
    reps: int = Field(default=10, ge=1, le=999)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    rest_s: int = Field(default=90, ge=0, le=3600, description="Rest after each set.")

    @field_validator("category", "exercise_name")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "").upper().strip()


class StrengthWorkoutSpec(ApiModel):
    """A strength workout to put on the Garmin calendar."""

    name: str = Field(min_length=1, max_length=80)
    exercises: list[StrengthExerciseSpec] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=1024)
    schedule_date: date | None = Field(
        default=None, description="If set, the workout is scheduled on this date after creation."
    )


class StrengthSessionSet(ApiModel):
    """One completed set."""

    reps: int | None = Field(default=None, ge=0, le=999)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    duration_s: float | None = Field(
        default=None, ge=0, description="Time under tension. Defaults to a few seconds per rep."
    )
    rest_s: float | None = Field(default=None, ge=0, description="Rest taken after this set.")


class StrengthSessionExercise(ApiModel):
    category: str
    exercise_name: str = ""
    sets: list[StrengthSessionSet] = Field(min_length=1)

    @field_validator("category", "exercise_name")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "").upper().strip()


class StrengthSessionSpec(ApiModel):
    """A completed strength session, to be written as an activity.

    This is the write Garmin's official developer programme has never offered:
    a finished workout with real sets, reps and weights, landing in the history
    as a Strength Training activity.
    """

    name: str = Field(default="Strength Training", max_length=80)
    start_time: datetime = Field(description="When the session started. Needs a timezone.")
    exercises: list[StrengthSessionExercise] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=1024)

    @field_validator("start_time")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        # Without an offset the encoder could only guess UTC, and a 7am
        # session in New York would land in Garmin at 3am.
        if value.tzinfo is None:
            raise ValueError(
                "start_time needs a timezone offset, e.g. 2026-09-21T07:00:00-04:00"
            )
        return value

    @property
    def total_sets(self) -> int:
        return sum(len(e.sets) for e in self.exercises)
