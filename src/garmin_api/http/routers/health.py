"""Health and wellness routes."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Path, Query

from ...models.health import (
    BodyCompositionSpec,
    DaySummary,
    HrvSummary,
    PersonalRecord,
    SleepSummary,
    StepsSummary,
    StressSummary,
    WeighIn,
)
from ...services import health as service
from ...services import weight as weight_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/day/{day}", operation_id="get_day_summary", response_model=DaySummary)
def day_summary(day: date) -> DaySummary:
    """Everything about one day in a single answer.

    Steps, calories, heart rate, stress, body battery, sleep, HRV, training
    readiness and body weight. Any field can be null if the watch was not worn
    or does not record that metric.
    """
    return service.day(day)


@router.get("/sleep/{day}", operation_id="get_sleep", response_model=SleepSummary | None)
def sleep(day: date) -> SleepSummary | None:
    """Sleep for one night, with stage breakdown and score."""
    return service.sleep(day)


@router.get("/hrv/{day}", operation_id="get_hrv", response_model=HrvSummary | None)
def hrv(
    day: date,
    include_readings: bool = Query(False, description="Add the five-minute readings."),
) -> HrvSummary | None:
    """Heart rate variability for one night: average, status and baseline."""
    return service.hrv_summary(day, include_readings=include_readings)


@router.get("/stress/{day}", operation_id="get_stress", response_model=StressSummary | None)
def stress(
    day: date,
    include_samples: bool = Query(False, description="Add the three-minute readings."),
) -> StressSummary | None:
    """A day of stress: average, peak, and minutes in each of Garmin's bands."""
    return service.stress_summary(day, include_samples=include_samples)


@router.get("/body-battery", operation_id="get_body_battery")
def body_battery(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(None, description="Last day, inclusive."),
) -> Any:
    """Body battery over a date range."""
    return service.body_battery(start, end)


@router.get("/steps/{day}", operation_id="get_steps", response_model=StepsSummary)
def steps(
    day: date,
    include_buckets: bool = Query(False, description="Add Garmin's quarter-hour buckets."),
) -> StepsSummary:
    """A day of steps: the total, and an hour-by-hour profile in GMT."""
    return service.steps_summary(day, include_buckets=include_buckets)


@router.get("/training-readiness/{day}", operation_id="get_training_readiness")
def training_readiness(day: date) -> Any:
    """Garmin's readiness score and the factors behind it."""
    return service.training_readiness(day)


@router.get("/training-status/{day}", operation_id="get_training_status")
def training_status(day: date) -> Any:
    """Training status, load and VO2 max context."""
    return service.training_status(day)


@router.get("/records", operation_id="get_personal_records", response_model=list[PersonalRecord])
def personal_records() -> list[PersonalRecord]:
    """Garmin's personal records, one entry each."""
    return service.records()


@router.get("/weight", operation_id="list_weigh_ins", response_model=list[WeighIn])
def weigh_ins(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(None, description="Last day, inclusive."),
) -> list[WeighIn]:
    """Every weigh-in over a date range, oldest first, with body composition.

    Includes every reading on days with more than one.
    """
    return weight_service.weigh_ins(start, end)


@router.post("/weight", operation_id="add_weigh_in")
def add_weigh_in(
    weight: float = Query(gt=0, le=1000),
    unit: str = Query("kg", pattern="^(kg|lbs)$"),
    timestamp: str | None = Query(None, description="ISO 8601. Defaults to now."),
) -> dict[str, Any]:
    """Record a body weight."""
    weight_service.add_weigh_in(weight, unit=unit, timestamp=timestamp)
    return {"status": "recorded", "weight": weight, "unit": unit}


@router.post("/body-composition", operation_id="add_body_composition")
def add_body_composition(spec: BodyCompositionSpec) -> dict[str, Any]:
    """Record a weigh-in with body composition: fat, water, muscle, bone, BMI.

    This is the way to bring a smart scale's full reading into Garmin. A plain
    add_weigh_in carries weight only. Garmin reports every upload as
    "processed"; an identical measurement sent again is still kept only once.
    """
    return weight_service.add_body_composition(spec)


@router.delete("/weight/{sample_pk}", operation_id="delete_weigh_in")
def delete_weigh_in(
    sample_pk: int = Path(gt=0), day: date = Query(description="The weigh-in's calendar date.")
) -> dict[str, Any]:
    """Delete one weigh-in. Takes the sample_pk that list_weigh_ins returns."""
    weight_service.delete_weigh_in(str(sample_pk), day)
    return {"sample_pk": sample_pk, "status": "deleted"}
