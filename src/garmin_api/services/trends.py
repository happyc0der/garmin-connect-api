"""Metrics over time, from Garmin's range endpoints.

One request covers most ranges: Garmin answers HRV, readiness, resting heart
rate and VO2 max for months at a time. Steps and sleep are fetched by the
library in 28-day pieces, Garmin's own limit for them. A range is capped at
two years so a single question cannot become hundreds of requests.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .. import auth
from ..errors import InvalidRequest
from ..models.common import as_date_str, grams_to_kg
from ..models.trends import (
    UNITS,
    Aggregation,
    Metric,
    Point,
    Trend,
    choose_aggregation,
    period_of,
    roll_up,
    summarise,
)

MAX_DAYS = 731


def trend(
    metric: Metric | str,
    start: date | str,
    end: date | str,
    *,
    aggregation: Aggregation | str | None = None,
) -> Trend:
    metric = Metric(metric)
    start_d = date.fromisoformat(as_date_str(start))
    end_d = date.fromisoformat(as_date_str(end))
    if end_d < start_d:
        raise InvalidRequest("The range ends before it starts.")
    if (end_d - start_d).days + 1 > MAX_DAYS:
        raise InvalidRequest(
            f"Ranges are limited to {MAX_DAYS} days.",
            detail="Ask for a shorter stretch, or for monthly points over two years.",
        )
    chosen = Aggregation(aggregation) if aggregation else choose_aggregation(start_d, end_d)
    daily = _daily(metric, start_d, end_d, chosen)
    native = metric in (Metric.vo2max, Metric.vo2max_cycling)
    # A week or month Garmin grouped itself may be labelled with its first
    # day, before a range that starts partway through it; it still belongs.
    first = period_of(start_d, chosen) if native else start_d
    daily = [p for p in daily if first <= p.period <= end_d]
    points = sorted(daily, key=lambda p: p.period) if native else roll_up(daily, chosen)
    return Trend(
        metric=metric,
        unit=UNITS[metric],
        start=start_d,
        end=end_d,
        aggregation=chosen,
        points=points,
        summary=summarise(points, daily),
    )


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _daily(metric: Metric, start: date, end: date, aggregation: Aggregation) -> list[Point]:
    s, e = start.isoformat(), end.isoformat()
    points: list[Point] = []

    if metric is Metric.steps:
        for row in auth.call("get_daily_steps", s, e) or []:
            day, steps = _day(row.get("calendarDate")), row.get("totalSteps")
            if day and steps is not None:
                points.append(
                    Point(period=day, value=float(steps), extra={"goal": row.get("stepGoal")})
                )

    elif metric is Metric.resting_hr:
        for row in auth.call("get_rhr_daily", s, e) or []:
            day, value = _day(row.get("calendarDate")), row.get("value")
            if day and value:
                points.append(Point(period=day, value=float(value)))

    elif metric in (Metric.sleep, Metric.sleep_score):
        for row in auth.call("get_sleep_daily", s, e) or []:
            day, values = _day(row.get("calendarDate")), row.get("values") or {}
            if not day:
                continue
            if metric is Metric.sleep and values.get("totalSleepTimeInSeconds"):
                points.append(
                    Point(period=day, value=round(values["totalSleepTimeInSeconds"] / 3600, 2))
                )
            elif metric is Metric.sleep_score and values.get("sleepScore") is not None:
                points.append(Point(period=day, value=float(values["sleepScore"])))

    elif metric is Metric.hrv:
        payload = auth.call("get_hrv_data_range", s, e) or {}
        for row in payload.get("hrvSummaries") or []:
            day, value = _day(row.get("calendarDate")), row.get("lastNightAvg")
            if day and value:
                points.append(
                    Point(period=day, value=float(value), extra={"status": row.get("status")})
                )

    elif metric is Metric.readiness:
        # Several entries a day; the morning score, as the day summary uses.
        from .health import morning_readiness

        rows_by_day: dict[date, list[dict[str, Any]]] = {}
        for row in auth.call("get_training_readiness_range", s, e) or []:
            day = _day(row.get("calendarDate"))
            if day:
                rows_by_day.setdefault(day, []).append(row)
        for day, rows in rows_by_day.items():
            chosen = morning_readiness(rows)
            if chosen is not None:
                points.append(
                    Point(
                        period=day,
                        value=float(chosen["score"]),
                        extra={"level": chosen.get("level")},
                    )
                )

    elif metric is Metric.stress:
        for row in auth.call("get_daily_stress_stats", s, e) or []:
            day, value = (
                _day(row.get("calendarDate")),
                (row.get("values") or {}).get("overallStressLevel"),
            )
            if day and value is not None and value >= 0:
                points.append(Point(period=day, value=float(value)))

    elif metric is Metric.body_battery:
        for row in auth.call("get_daily_body_battery_stats", s, e) or []:
            day, values = _day(row.get("calendarDate")), row.get("values") or {}
            if day and values.get("highBodyBattery") is not None:
                points.append(
                    Point(
                        period=day,
                        value=float(values["highBodyBattery"]),
                        extra={"low": values.get("lowBodyBattery")},
                    )
                )

    elif metric in (Metric.weight, Metric.body_fat):
        payload = auth.call("get_weigh_ins", s, e) or {}
        for row in payload.get("dailyWeightSummaries") or []:
            day, latest = _day(row.get("summaryDate")), row.get("latestWeight") or {}
            if not day:
                continue
            if metric is Metric.weight and latest.get("weight"):
                kg = grams_to_kg(latest["weight"])
                if kg is not None:
                    points.append(Point(period=day, value=round(kg, 2)))
            elif metric is Metric.body_fat and latest.get("bodyFat") is not None:
                points.append(Point(period=day, value=float(latest["bodyFat"])))

    elif metric in (Metric.vo2max, Metric.vo2max_cycling):
        # Garmin recomputes VO2 max only after qualifying runs or rides, so
        # daily ranges are mostly empty; it aggregates natively. Running
        # ("generic") and cycling are separate estimates and stay separate.
        native = {"day": "daily", "week": "weekly", "month": "monthly"}[aggregation.value]
        sport = "generic" if metric is Metric.vo2max else "cycling"
        for row in auth.call("get_max_metrics_range", s, e, aggregation=native) or []:
            entry = row.get(sport) or {}
            value = entry.get("vo2MaxPreciseValue") or entry.get("vo2MaxValue")
            day = _day(entry.get("calendarDate"))
            if day and value:
                grouped = aggregation is not Aggregation.day
                points.append(Point(period=day, value=float(value), days=None if grouped else 1))

    return points


def today() -> date:
    """The current date in the Garmin profile's timezone."""
    value = auth.call("today")
    return value if isinstance(value, date) else datetime.now().date()
