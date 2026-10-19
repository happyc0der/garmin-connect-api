"""Metrics over time, from Garmin's range endpoints.

One request covers most ranges: Garmin answers HRV, readiness, resting heart
rate and VO2 max for months at a time. Steps and sleep are fetched by the
library in 28-day pieces, Garmin's own limit for them. A range is capped at
two years so a single question cannot become hundreds of requests.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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

# The longest range, in days, each endpoint answers in one request, found live
# on 19 Oct 2026: stress and body battery refuse more than 28 days ("difference
# in days ... cannot be more than 28"), HRV more than 366 days apart. Longer
# ranges are fetched in windows; steps are already split by the library.
WINDOW_DAYS = {
    "get_daily_stress_stats": 28,
    "get_daily_body_battery_stats": 28,
    "get_hrv_data_range": 365,
    "get_training_readiness_range": 28,
    # The library documents about a year per request for resting heart rate.
    "get_rhr_daily": 365,
}


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
    if start_d.year < 1990 or end_d.year > 2099:
        raise InvalidRequest("Dates must fall between 1990 and 2099.")
    if (end_d - start_d).days + 1 > MAX_DAYS:
        raise InvalidRequest(
            f"Ranges are limited to {MAX_DAYS} days.",
            detail="Ask for a shorter stretch, or for monthly points over two years.",
        )
    chosen = Aggregation(aggregation) if aggregation else choose_aggregation(start_d, end_d)
    # One point a day, even if two windows both answered for the day between them.
    daily = list({p.period: p for p in _daily(metric, start_d, end_d, chosen)}.values())
    native = metric in (Metric.vo2max, Metric.vo2max_cycling)
    # A week or month Garmin grouped itself may be labelled with its first
    # day, before a range that starts partway through it; it still belongs.
    first = period_of(start_d, chosen) if native else start_d
    daily = [p for p in daily if first <= p.period <= end_d]
    points = sorted(daily, key=lambda p: p.period) if native else roll_up(daily, chosen)
    # The change is between whole weeks or months: a range ending today cuts
    # its last week short, and a stub of one day says little about a trend.
    whole = [p for p in points if _whole(p.period, chosen, start_d, end_d)]
    return Trend(
        metric=metric,
        unit=UNITS[metric],
        start=start_d,
        end=end_d,
        aggregation=chosen,
        points=points,
        summary=summarise(whole if len(whole) >= 2 else points, daily),
    )


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _whole(period: date, aggregation: Aggregation, start: date, end: date) -> bool:
    """Whether the range covers all of the week or month that starts on ``period``."""
    if aggregation is Aggregation.day:
        return True
    if aggregation is Aggregation.week:
        last = period + timedelta(days=6)
    else:
        following = (period.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = following - timedelta(days=1)
    return start <= period and last <= end


def _windows(start: date, end: date, days: int) -> list[tuple[str, str]]:
    """``start`` to ``end`` as consecutive ranges of at most ``days`` days."""
    out: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=days - 1))
        out.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + timedelta(days=1)
    return out


def _rows(fn_name: str, start: date, end: date, key: str | None = None) -> list[Any]:
    """Every row a range endpoint gives for ``start`` to ``end``, a window at a time.

    ``key`` names the list inside a dict reply, for endpoints that wrap theirs.
    """
    rows: list[Any] = []
    for s, e in _windows(start, end, WINDOW_DAYS.get(fn_name, MAX_DAYS)):
        reply = auth.call(fn_name, s, e)
        if key is not None:
            reply = reply.get(key) if isinstance(reply, dict) else None
        elif isinstance(reply, dict):
            reply = [reply]  # a single entry, as readiness sometimes answers
        rows.extend(r for r in reply or [] if isinstance(r, dict))
    return rows


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
        for row in _rows("get_rhr_daily", start, end):
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
        for row in _rows("get_hrv_data_range", start, end, key="hrvSummaries"):
            day, value = _day(row.get("calendarDate")), row.get("lastNightAvg")
            if day and value:
                points.append(
                    Point(period=day, value=float(value), extra={"status": row.get("status")})
                )

    elif metric is Metric.readiness:
        # Several entries a day; the morning score, as the day summary uses.
        from .health import morning_readiness

        rows_by_day: dict[date, list[dict[str, Any]]] = {}
        for row in _rows("get_training_readiness_range", start, end):
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
        for row in _rows("get_daily_stress_stats", start, end):
            day, value = (
                _day(row.get("calendarDate")),
                (row.get("values") or {}).get("overallStressLevel"),
            )
            if day and value is not None and value >= 0:
                points.append(Point(period=day, value=float(value)))

    elif metric is Metric.body_battery:
        for row in _rows("get_daily_body_battery_stats", start, end):
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
            elif metric is Metric.body_fat:
                # The day's last reading with body fat in it: a plain weigh-in
                # after a scale's full reading must not lose the day's figure.
                readings = [
                    m
                    for m in row.get("allWeightMetrics") or [latest]
                    if isinstance(m, dict) and m.get("bodyFat") is not None
                ]
                if readings:
                    last = max(readings, key=lambda m: m.get("timestampGMT") or m.get("date") or 0)
                    points.append(Point(period=day, value=float(last["bodyFat"])))

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
