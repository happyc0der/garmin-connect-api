"""Activities, and the per-set strength detail that is the point of all this.

Garmin's own field names are kept in ``raw`` so nothing is lost, but the typed
surface is normalised: weights in kilograms, instants as timezone-aware
datetimes, durations in seconds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import Field, computed_field, model_validator

from .common import ApiModel, first, grams_to_kg, parse_dt, parse_local

STRENGTH_TYPES = frozenset({"strength_training"})


class ActivityType(ApiModel):
    type_id: int | None = None
    type_key: str | None = Field(default=None, description='e.g. "strength_training".')
    parent_type_id: int | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any] | None) -> ActivityType | None:
        if not payload:
            return None
        return cls(
            type_id=payload.get("typeId"),
            type_key=payload.get("typeKey"),
            parent_type_id=payload.get("parentTypeId"),
        )


class ActivitySummary(ApiModel):
    """One activity as it appears in a list."""

    activity_id: int
    name: str | None = None
    activity_type: ActivityType | None = None
    start_time: datetime | None = Field(default=None, description="Start instant, UTC.")
    start_time_local: str | None = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
        examples=["2026-08-30T18:15:00"],
        description=(
            "Wall-clock start where the activity happened, as YYYY-MM-DDTHH:MM:SS. "
            "Deliberately has no timezone: Garmin gives the time, not the zone."
        ),
    )
    duration_s: float | None = None
    elapsed_duration_s: float | None = None
    moving_duration_s: float | None = None
    distance_m: float | None = None
    calories: float | None = None
    average_hr: float | None = None
    max_hr: float | None = None
    steps: int | None = None
    has_sets: bool = Field(
        default=False,
        description="Whether this is a strength activity, the kind that carries per-set detail.",
    )
    raw: dict[str, Any] | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_raw: bool = False) -> Self:
        original = payload
        payload = _flatten_detail(payload)
        return cls(
            activity_id=int(first(payload, "activityId", "activityid") or 0),
            name=first(payload, "activityName", "activity_name"),
            activity_type=ActivityType.from_garmin(payload.get("activityType")),
            start_time=parse_dt(first(payload, "startTimeGMT", "beginTimestamp")),
            start_time_local=parse_local(payload.get("startTimeLocal")),
            duration_s=first(payload, "duration"),
            elapsed_duration_s=first(payload, "elapsedDuration"),
            moving_duration_s=first(payload, "movingDuration"),
            distance_m=first(payload, "distance"),
            calories=first(payload, "calories"),
            average_hr=first(payload, "averageHR", "avgHr"),
            max_hr=first(payload, "maxHR", "maxHr"),
            steps=first(payload, "steps"),
            # Only strength activities carry sets. Swims have laps and stroke
            # counts, which are a different thing and must not read as sets.
            has_sets=(payload.get("activityType") or {}).get("typeKey") in STRENGTH_TYPES,
            raw=original if include_raw else None,
        )


def _flatten_detail(payload: dict[str, Any]) -> dict[str, Any]:
    """Give the single-activity response the list entry's flat shape.

    The list endpoint puts every field at the top level. Fetching one activity
    nests the same fields under ``summaryDTO`` and renames ``activityType`` to
    ``activityTypeDTO``, so without this a fetched activity came back with no
    type and no start time.
    """
    summary = payload.get("summaryDTO")
    if not isinstance(summary, dict):
        return payload
    flat = {**summary, **{k: v for k, v in payload.items() if k != "summaryDTO"}}
    flat.setdefault("activityType", payload.get("activityTypeDTO"))
    return flat


class Activity(ActivitySummary):
    """A single activity fetched on its own.

    Same shape as the list entry; Garmin returns more fields here, and they
    live in ``raw``.
    """


class StrengthSet(ApiModel):
    """One set of one exercise.

    ``set_type`` is ``ACTIVE`` for a working set and ``REST`` for the gap
    between them; Garmin records both, and the rests are usually not what a
    caller wants, so services filter them by default.
    """

    set_number: int | None = Field(
        default=None,
        description="Working sets numbered 1, 2, 3 in order; rests have none. Edits use this.",
    )
    message_index: int | None = Field(default=None, description="Garmin's own index for the row.")
    set_type: str | None = None
    exercise_category: str | None = Field(default=None, description='e.g. "BENCH_PRESS".')
    exercise_name: str | None = Field(default=None, description='e.g. "BARBELL_BENCH_PRESS".')
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: float | None = None
    start_time: datetime | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_rest(self) -> bool:
        return (self.set_type or "").upper() == "REST"

    @classmethod
    def from_garmin(
        cls, payload: dict[str, Any], *, index: int | None = None, include_raw: bool = False
    ) -> StrengthSet:
        # Garmin nests the exercise under a list, ranked by its own guess at
        # what the wrist movement was. The first entry is the chosen one.
        exercises = payload.get("exercises") or []
        chosen = exercises[0] if exercises else {}
        return cls(
            set_number=index,
            message_index=payload.get("messageIndex"),
            set_type=first(payload, "setType"),
            exercise_category=chosen.get("category"),
            exercise_name=chosen.get("name"),
            reps=first(payload, "repetitionCount", "repetitions", "reps"),
            weight_kg=grams_to_kg(first(payload, "weight", "weightValue")),
            duration_s=first(payload, "duration"),
            start_time=parse_dt(first(payload, "startTime", "startTimeGMT")),
            raw=payload if include_raw else None,
        )


class ActivitySets(ApiModel):
    """Every set of one activity, with a little arithmetic already done."""

    activity_id: int
    sets: list[StrengthSet] = Field(default_factory=list)

    @property
    def working_sets(self) -> list[StrengthSet]:
        return [s for s in self.sets if not s.is_rest]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_volume_kg(self) -> float:
        """Sum of reps times weight over the working sets.

        The usual definition of training volume. Sets with no weight recorded
        (bodyweight work) contribute nothing, which is the conventional
        treatment rather than a claim that they were easy.
        """
        return round(
            sum((s.reps or 0) * (s.weight_kg or 0.0) for s in self.working_sets),
            2,
        )

    @classmethod
    def from_garmin(
        cls, activity_id: int, payload: dict[str, Any], *, include_raw: bool = False
    ) -> ActivitySets:
        # Numbered by position among working sets. Garmin's messageIndex
        # starts at 0 and counts rests too, so using it gave the first two
        # rows the same number and gave rests numbers of their own.
        sets: list[StrengthSet] = []
        number = 0
        for row in payload.get("exerciseSets") or []:
            rest = str(row.get("setType") or "").upper() == "REST"
            if not rest:
                number += 1
            sets.append(
                StrengthSet.from_garmin(
                    row, index=None if rest else number, include_raw=include_raw
                )
            )
        return cls(activity_id=activity_id, sets=sets)


class SetChange(ApiModel):
    """A change to one or more working sets, by their set_number.

    Give the exercise as Garmin's category and exercise_name pair; leave out
    what should stay as it is.
    """

    sets: list[int] = Field(
        min_length=1, description="Working set numbers, as get_activity_sets shows."
    )
    category: str | None = Field(default=None, description='e.g. "ROW".')
    exercise_name: str | None = Field(
        default=None, description='e.g. "DUMBBELL_ROW". Empty for just the category.'
    )
    reps: int | None = Field(default=None, ge=0, le=999)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _something(self) -> SetChange:
        if self.exercise_name and not self.category:
            raise ValueError("give the category with an exercise_name")
        if self.category is None and self.reps is None and self.weight_kg is None:
            raise ValueError("give an exercise, reps or weight_kg to change")
        return self


class SetEdit(ApiModel):
    """Corrections to an activity's sets: change some, delete some, or both."""

    changes: list[SetChange] = Field(default_factory=list)
    delete: list[int] = Field(default_factory=list, description="Working set numbers to remove.")
