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
from ...models.trends import Aggregation, Metric, Trend
from ...services import health as service
from ...services import weight as weight_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/day/{day}", operation_id="get_day_summary", response_model=DaySummary)
def day_summary(day: date = Path(description="The calendar date, YYYY-MM-DD.")) -> DaySummary:
    """Return one calendar day's numbers in a single answer: steps, calories, heart
    rate, stress, body battery, last night's sleep, HRV, the morning readiness score
    and the day's last weigh-in.

    It does not list the day's activities: use list_activities with start_date and
    end_date both set to the day. For one metric, its own tool (get_sleep,
    get_hrv, get_stress, get_steps, get_training_readiness) gives more detail in
    one Garmin request instead of five; for seven days use week_summary, for a
    range get_trend. A part whose request fails is null and named under
    unavailable; if every part fails, or Garmin rate limits, the call returns an
    error instead. A night's sleep belongs to the day it ended on.
    """
    return service.day(day)


@router.get("/sleep/{day}", operation_id="get_sleep", response_model=SleepSummary | None)
def sleep(
    day: date = Path(description="The date the night ended on; last night is today."),
) -> SleepSummary | None:
    """Return one night's sleep: start and end, total, deep, light, REM and awake
    time, sleep score and average respiration.

    A night belongs to the day it ended on: last night is today's date. Null when
    no sleep was recorded. The same summary is inside get_day_summary, which costs
    five Garmin requests to this one's one; for sleep hours or score over many
    nights use get_trend with metric sleep or sleep_score.
    """
    return service.sleep(day)


@router.get("/hrv/{day}", operation_id="get_hrv", response_model=HrvSummary | None)
def hrv(
    day: date = Path(description="The date the night ended on; last night is today."),
    include_readings: bool = Query(False, description="Add the five-minute readings."),
) -> HrvSummary | None:
    """Return one night's heart rate variability in milliseconds: the night's average
    and five-minute high, the weekly average, Garmin's status (such as BALANCED,
    UNBALANCED or LOW) and the baseline band.

    A night belongs to the day it ended on: last night is today's date. With no
    HRV for that night the reply is null, or its fields are. The nightly
    average and status are also in get_day_summary; over weeks or months use
    get_trend with metric hrv.
    include_readings adds the five-minute readings.
    """
    return service.hrv_summary(day, include_readings=include_readings)


@router.get("/stress/{day}", operation_id="get_stress", response_model=StressSummary | None)
def stress(
    day: date = Path(description="The calendar date, YYYY-MM-DD."),
    include_samples: bool = Query(False, description="Add the three-minute readings."),
) -> StressSummary | None:
    """Return one day's stress: the average and peak, and the minutes at rest, in
    Garmin's low, medium and high bands, and unmeasured.

    The day's average alone is also in get_day_summary; for the daily average over
    weeks or months use get_trend with metric stress. With no stress data for
    the day the reply is null, or its fields are. include_samples adds the
    three-minute readings, which run to hundreds; leave it off unless the shape
    of the day matters.
    """
    return service.stress_summary(day, include_samples=include_samples)


@router.get("/body-battery", operation_id="get_body_battery")
def body_battery(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(
        None, description="Last day, inclusive. Left out, only the start day."
    ),
) -> Any:
    """Return Garmin's own body battery record for each day from start to end, unprocessed.

    Use it for the detail behind a day's number. For one day's high and low,
    get_day_summary already has them; for daily highs over weeks or months, with
    an average and the change, use get_trend with metric body_battery. One Garmin
    request whatever the range, so keep it to days. The reply is Garmin's JSON
    as it comes: field names are Garmin's and nothing is converted.
    """
    return service.body_battery(start, end)


@router.get("/steps/{day}", operation_id="get_steps", response_model=StepsSummary)
def steps(
    day: date = Path(description="The calendar date, YYYY-MM-DD."),
    include_buckets: bool = Query(False, description="Add Garmin's quarter-hour buckets."),
) -> StepsSummary:
    """Return one day's steps: the total, and 24 hourly totals by GMT hour, not local time.

    For the total beside the day's other numbers use get_day_summary; for daily
    totals over a range with an average and the change, get_trend with metric
    steps. total is null, not 0, when Garmin did not answer. include_buckets adds
    Garmin's quarter-hour buckets.
    """
    return service.steps_summary(day, include_buckets=include_buckets)


@router.get("/training-readiness/{day}", operation_id="get_training_readiness")
def training_readiness(day: date = Path(description="The calendar date, YYYY-MM-DD.")) -> Any:
    """Return every training readiness entry Garmin recorded for one day: the score,
    its level and the factors behind it.

    Use it for why the score is what it is. Garmin recomputes readiness through
    the day; the entry whose inputContext is AFTER_WAKEUP_RESET is the morning
    score, which get_day_summary gives on its own and get_trend with metric
    readiness follows over weeks or months. One Garmin request. The reply is
    Garmin's JSON as it comes, usually a list of entries; null or empty when
    nothing was recorded.
    """
    return service.training_readiness(day)


@router.get("/training-status/{day}", operation_id="get_training_status")
def training_status(day: date = Path(description="The date to report on, YYYY-MM-DD.")) -> Any:
    """Return Garmin's training status for one date: the status, training load and
    VO2 max context behind it, as Garmin's unprocessed JSON.

    For VO2 max over time use get_trend with metric vo2max (running) or
    vo2max_cycling; for whether to train today, get_training_readiness. One Garmin
    request; null when Garmin reports nothing for the date. Field names are Garmin's
    own.
    """
    return service.training_status(day)


@router.get("/records", operation_id="get_personal_records", response_model=list[PersonalRecord])
def personal_records() -> list[PersonalRecord]:
    """List every personal record Garmin holds for the account, each named and in
    units (e.g. "Fastest 1 km run"), newest first; records with no date come last.

    A record set in an activity carries its activity_id, which get_activity
    opens; step and goal-streak records have none. For
    how a metric moved over a range use get_trend. Two Garmin requests the first
    time (the record types are then kept for the process), one after.
    """
    return service.records()


@router.get("/weight", operation_id="list_weigh_ins", response_model=list[WeighIn])
def weigh_ins(
    start: date = Query(description="First day, inclusive."),
    end: date | None = Query(None, description="Last day, inclusive."),
) -> list[WeighIn]:
    """List every weigh-in from start to end (end defaults to start), oldest first,
    with body composition and the sample_pk that delete_weigh_in takes.

    Includes every reading on days with more than one. For weight or body fat as
    one value a day with an average and the change, use get_trend with metric
    weight or body_fat; get_day_summary gives the day's last weigh-in.
    """
    return weight_service.weigh_ins(start, end)


@router.post("/weight", operation_id="add_weigh_in")
def add_weigh_in(
    weight: float = Query(gt=0, le=1000, description="In the given unit."),
    unit: str = Query("kg", pattern="^(kg|lbs)$", description="kg or lbs."),
    timestamp: str | None = Query(None, description="ISO 8601. Defaults to now."),
) -> dict[str, Any]:
    """Record one body weight in Garmin Connect, in kg or lbs, with no body composition.

    For a smart scale reading with fat, water, muscle or bone, use
    add_body_composition instead. Without a timestamp it is filed now. A timestamp
    without an offset is taken as local time in the Garmin account's timezone, and
    one with an offset is moved into that timezone, so it lands on the right day.
    To correct a reading, delete it with delete_weigh_in (list_weigh_ins gives its
    sample_pk) rather than adding another.
    """
    weight_service.add_weigh_in(weight, unit=unit, timestamp=timestamp)
    return {"status": "recorded", "weight": weight, "unit": unit}


@router.post("/body-composition", operation_id="add_body_composition")
def add_body_composition(spec: BodyCompositionSpec) -> dict[str, Any]:
    """Record a weigh-in with body composition: fat, water, muscle, bone, BMI.

    This is the way to bring a smart scale's full reading into Garmin. A plain
    add_weigh_in carries weight only. Garmin reports every upload as
    "processed"; an identical measurement sent again is still kept only once,
    but a different reading at the same moment is kept as a second weigh-in.
    To correct one, delete the wrong one first: list_weigh_ins gives its id.
    """
    return weight_service.add_body_composition(spec)


@router.delete("/weight/{sample_pk}", operation_id="delete_weigh_in")
def delete_weigh_in(
    sample_pk: int = Path(gt=0, description="From list_weigh_ins."),
    day: date = Query(description="The weigh-in's calendar date."),
) -> dict[str, Any]:
    """Permanently delete one weigh-in, identified by the sample_pk and day that
    list_weigh_ins gives for it.

    Use it to remove a wrong or duplicate reading; to correct one, delete it and
    record the right value with add_weigh_in or add_body_composition. Nothing here
    restores it, and day summaries and weight trends change with it.
    """
    weight_service.delete_weigh_in(str(sample_pk), day)
    return {"sample_pk": sample_pk, "status": "deleted"}


@router.get("/trends/{metric}", operation_id="get_trend", response_model=Trend)
def get_trend(
    metric: Metric = Path(description="One of the metrics listed above."),
    start: date = Query(description="First day, inclusive."),
    end: date = Query(description="Last day, inclusive."),
    aggregation: Aggregation | None = Query(
        None, description="day, week or month. Chosen from the range when left out."
    ),
) -> Trend:
    """How one metric moved over a range, with average, extremes and change.

    Metrics: steps, resting_hr, sleep (hours), sleep_score, hrv, readiness
    (the morning score), stress (the day's average), body_battery (the day's
    high), weight, body_fat, vo2max (running), vo2max_cycling. Up to two months
    comes back daily, up to a year weekly, longer monthly. Ranges are limited
    to two years. Steps, sleep, sleep_score, stress and body_battery are
    fetched four weeks per Garmin request, so a year of one is about 14
    requests; hrv, readiness and resting_hr a year per request; weight,
    body_fat and VO2 max one request.

    For one day use get_day_summary or that metric's own tool; for every
    weigh-in rather than one a day, list_weigh_ins.

    The summary gives days_with_data (periods_with_data for VO2 max, which
    Garmin groups itself), the average, min and max of the days, the first and
    last day, and change: the last whole period's average minus the first
    whole period's, so a week the range cuts short does not count.
    """
    from ...services import trends

    return trends.trend(metric, start, end, aggregation=aggregation)
