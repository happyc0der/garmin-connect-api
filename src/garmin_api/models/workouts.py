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
from typing import Annotated, Any, Literal

from pydantic import Discriminator, Field, Tag, field_validator, model_validator

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
    # A day at most: well past any real set, and inside the FIT field's range.
    duration_s: float | None = Field(
        default=None,
        ge=0,
        le=86400,
        description="Time under tension. Defaults to a few seconds per rep.",
    )
    rest_s: float | None = Field(
        default=None, ge=0, le=86400, description="Rest taken after this set."
    )


class StrengthSessionExercise(ApiModel):
    category: str
    exercise_name: str = Field(
        default="", description="Garmin's exercise name. Left empty, the category's generic one."
    )
    sets: list[StrengthSessionSet] = Field(min_length=1)

    @field_validator("category", "exercise_name")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "").upper().strip()

    @model_validator(mode="after")
    def _generic(self) -> StrengthSessionExercise:
        # Garmin's generic entry repeats the category ("BENCH_PRESS" /
        # "BENCH_PRESS"), and every category has one.
        if not self.exercise_name:
            self.exercise_name = self.category
        return self


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
            raise ValueError("start_time needs a timezone offset, e.g. 2026-09-21T07:00:00-04:00")
        return value

    @property
    def total_sets(self) -> int:
        return sum(len(e.sets) for e in self.exercises)


# --- Swim, run and ride workouts ---------------------------------------------

STROKES = {
    "any": (1, "any_stroke"),
    "back": (2, "backstroke"),
    "breast": (3, "breaststroke"),
    "drill": (4, "drill"),
    "fly": (5, "fly"),
    "free": (6, "free"),
    "im": (7, "individual_medley"),
    "mixed": (8, "mixed"),
}
EQUIPMENT = {
    "fins": (1, "fins"),
    "kickboard": (2, "kickboard"),
    "paddles": (3, "paddles"),
    "pull_buoy": (4, "pull_buoy"),
    "snorkel": (5, "snorkel"),
}
# Garmin's own ids, from /workout-service/workout/types, read 19 Oct 2026.
StepKind = Literal["warmup", "interval", "recovery", "rest", "cooldown"]
DISTANCE_UNITS = {"m": 1.0, "km": 1000.0, "mi": 1609.344, "yd": 0.9144}


class Target(ApiModel):
    """What to hold during a step. Give exactly one.

    Paces are "m:ss" strings, slower first: ``pace_per_km=["5:30", "5:00"]``.
    """

    pace_per_km: tuple[str, str] | None = None
    pace_per_mile: tuple[str, str] | None = None
    speed_kph: tuple[float, float] | None = None
    heart_rate_zone: int | None = Field(default=None, ge=1, le=5)
    heart_rate_bpm: tuple[int, int] | None = None
    power_zone: int | None = Field(default=None, ge=1, le=7)
    power_watts: tuple[int, int] | None = None
    cadence: tuple[int, int] | None = Field(
        default=None, description="Steps or revolutions a minute."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> Target:
        given = [name for name, value in self.__dict__.items() if value is not None]
        if len(given) != 1:
            raise ValueError(f"give exactly one target, got {given or 'none'}")
        for name in ("pace_per_km", "pace_per_mile"):
            value = getattr(self, name)
            if value is not None:
                for text in value:
                    _seconds(text)
        return self


def _seconds(text: str) -> int:
    """'5:30' -> 330."""
    try:
        minutes, seconds = text.strip().split(":")
        return int(minutes) * 60 + int(seconds)
    except ValueError as exc:
        raise ValueError(f"pace {text!r} should look like 5:30") from exc


class EnduranceStep(ApiModel):
    """One step: ends after a time, a distance, or a press of the lap button."""

    kind: StepKind = "interval"
    duration_s: float | None = Field(default=None, gt=0, le=86400)
    distance: float | None = Field(default=None, gt=0)
    distance_unit: Literal["m", "km", "mi", "yd"] = "m"
    lap_button: bool = False
    target: Target | None = None
    stroke: Literal["any", "back", "breast", "drill", "fly", "free", "im", "mixed"] | None = None
    equipment: Literal["fins", "kickboard", "paddles", "pull_buoy", "snorkel"] | None = None
    note: str | None = Field(default=None, max_length=200, description="Shown on the watch.")

    @model_validator(mode="after")
    def _one_end(self) -> EnduranceStep:
        ends = [self.duration_s is not None, self.distance is not None, self.lap_button]
        if sum(ends) != 1:
            raise ValueError("give exactly one of duration_s, distance or lap_button")
        return self

    @property
    def distance_m(self) -> float | None:
        return self.distance * DISTANCE_UNITS[self.distance_unit] if self.distance else None


class EnduranceRepeat(ApiModel):
    repeat: int = Field(ge=2, le=99)
    steps: list[EnduranceStep] = Field(min_length=1)


def _step_or_repeat(value: Any) -> str:
    """Tell a repeat from a step by its ``repeat`` count, so errors name the real fault."""
    if isinstance(value, dict):
        return "repeat" if "repeat" in value else "step"
    return "repeat" if isinstance(value, EnduranceRepeat) else "step"


StepOrRepeat = Annotated[
    Annotated[EnduranceStep, Tag("step")] | Annotated[EnduranceRepeat, Tag("repeat")],
    Discriminator(_step_or_repeat),
]


class EnduranceWorkoutSpec(ApiModel):
    """A run, ride or pool swim to put on the Garmin calendar."""

    sport: Literal["running", "cycling", "swimming"]
    name: str = Field(min_length=1, max_length=80)
    steps: list[StepOrRepeat] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=1024)
    pool_length: float | None = Field(default=None, gt=0, le=100, description="Swims only.")
    pool_unit: Literal["m", "yd"] = "m"
    schedule_date: date | None = None

    @model_validator(mode="after")
    def _swim_rules(self) -> EnduranceWorkoutSpec:
        flat = [
            s
            for item in self.steps
            for s in (item.steps if isinstance(item, EnduranceRepeat) else [item])
        ]
        if self.sport != "swimming":
            if any(s.stroke or s.equipment for s in flat):
                raise ValueError("stroke and equipment are for swims")
            if self.pool_length:
                raise ValueError("pool_length is for swims")
            return self
        if any(s.target for s in flat):
            raise ValueError(
                "pace and heart-rate targets are for runs and rides; describe swim effort in a note"
            )
        if self.pool_length:
            pool_m = self.pool_length * DISTANCE_UNITS[self.pool_unit]
            for s in flat:
                metres = s.distance_m
                if metres and abs(metres / pool_m - round(metres / pool_m)) > 0.01:
                    raise ValueError(
                        f"{s.distance:g} {s.distance_unit} is not a whole number of "
                        f"{self.pool_length:g} {self.pool_unit} lengths"
                    )
        return self
