"""A metric over a stretch of days, weeks or months, in one shape.

Every metric comes back as the same list of ``{period, value}`` points and the
same summary, so "how has my resting heart rate moved since summer?" and "has
my HRV recovered?" read alike. Long ranges are rolled up into weeks or months
so a year of daily points does not flood a context window.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import Field

from .common import ApiModel


class Metric(StrEnum):
    steps = "steps"
    resting_hr = "resting_hr"
    sleep = "sleep"
    sleep_score = "sleep_score"
    hrv = "hrv"
    readiness = "readiness"
    stress = "stress"
    body_battery = "body_battery"
    weight = "weight"
    body_fat = "body_fat"
    vo2max = "vo2max"
    vo2max_cycling = "vo2max_cycling"


UNITS = {
    Metric.steps: "steps",
    Metric.resting_hr: "bpm",
    Metric.sleep: "hours",
    Metric.sleep_score: "score",
    Metric.hrv: "ms",
    Metric.readiness: "score",
    Metric.stress: "level",
    Metric.body_battery: "level",
    Metric.weight: "kg",
    Metric.body_fat: "%",
    Metric.vo2max: "ml/kg/min",
    Metric.vo2max_cycling: "ml/kg/min",
}

# Metrics where a day's total matters when rolling up, rather than its mean.
SUMMED: set[Metric] = set()


class Aggregation(StrEnum):
    day = "day"
    week = "week"
    month = "month"


class Point(ApiModel):
    period: date = Field(description="The day, or the first day of the week or month.")
    value: float
    days: int | None = Field(
        default=1,
        description=(
            "Days of data behind a weekly or monthly point; None when Garmin grouped the "
            "period itself and did not say."
        ),
    )
    extra: dict[str, Any] | None = Field(
        default=None, description="Metric-specific detail, e.g. HRV status or body battery low."
    )


class Trend(ApiModel):
    metric: Metric
    unit: str
    start: date
    end: date
    aggregation: Aggregation
    points: list[Point] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


def choose_aggregation(start: date, end: date) -> Aggregation:
    """Days up to two months, weeks up to a year, months beyond."""
    days = (end - start).days + 1
    if days <= 62:
        return Aggregation.day
    if days <= 366:
        return Aggregation.week
    return Aggregation.month


def period_of(day: date, aggregation: Aggregation) -> date:
    if aggregation is Aggregation.week:
        return date.fromordinal(day.toordinal() - day.weekday())
    if aggregation is Aggregation.month:
        return day.replace(day=1)
    return day


def roll_up(daily: list[Point], aggregation: Aggregation) -> list[Point]:
    """Average daily points into weeks or months. Days without data are not zeros."""
    if aggregation is Aggregation.day:
        return sorted(daily, key=lambda p: p.period)
    groups: dict[date, list[Point]] = {}
    for point in daily:
        groups.setdefault(period_of(point.period, aggregation), []).append(point)
    return [
        Point(
            period=period,
            value=round(sum(p.value for p in group) / len(group), 1),
            days=len(group),
        )
        for period, group in sorted(groups.items())
    ]


def summarise(points: list[Point], daily: list[Point]) -> dict[str, Any]:
    if not daily:
        return {"days_with_data": 0}
    values = [p.value for p in daily]
    first, last = (
        sorted(daily, key=lambda p: p.period)[0],
        sorted(daily, key=lambda p: p.period)[-1],
    )
    # Points Garmin grouped itself say nothing about how many days lie behind
    # them, so those are counted as periods.
    counted = "days_with_data" if all(p.days == 1 for p in daily) else "periods_with_data"
    summary: dict[str, Any] = {
        counted: len(daily),
        "average": round(sum(values) / len(values), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
        "first": {"day": first.period.isoformat(), "value": first.value},
        "last": {"day": last.period.isoformat(), "value": last.value},
        "change": round(last.value - first.value, 1),
    }
    if len(points) >= 2:
        # Over weeks or months, the change is the movement of the averages:
        # two single days at either end say little about a trend.
        summary["first_period_average"] = points[0].value
        summary["last_period_average"] = points[-1].value
        summary["change"] = round(points[-1].value - points[0].value, 1)
    return summary
