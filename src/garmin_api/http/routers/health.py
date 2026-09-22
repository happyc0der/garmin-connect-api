"""Health and wellness routes."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query

from ...models.health import DaySummary, SleepSummary, WeighIn
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


@router.get("/hrv/{day}", operation_id="get_hrv")
def hrv(day: date) -> Any:
    """Heart rate variability for one night."""
    return service.hrv(day)


@router.get("/stress/{day}", operation_id="get_stress")
def stress(day: date) -> Any:
    """All-day stress levels."""
    return service.stress(day)


@router.get("/body-battery", operation_id="get_body_battery")
def body_battery(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(None, description="Last day, inclusive."),
) -> Any:
    """Body battery over a date range."""
    return service.body_battery(start, end)


@router.get("/steps/{day}", operation_id="get_steps")
def steps(day: date) -> Any:
    """Step counts through one day."""
    return service.steps(day)


@router.get("/training-readiness/{day}", operation_id="get_training_readiness")
def training_readiness(day: date) -> Any:
    """Garmin's readiness score and the factors behind it."""
    return service.training_readiness(day)


@router.get("/training-status/{day}", operation_id="get_training_status")
def training_status(day: date) -> Any:
    """Training status, load and VO2 max context."""
    return service.training_status(day)


@router.get("/records", operation_id="get_personal_records")
def personal_records() -> Any:
    """Garmin's personal records."""
    return service.personal_records()


@router.get("/weight", operation_id="list_weigh_ins", response_model=list[WeighIn])
def weigh_ins(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(None, description="Last day, inclusive."),
) -> list[WeighIn]:
    """Body weight and composition entries over a date range."""
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
