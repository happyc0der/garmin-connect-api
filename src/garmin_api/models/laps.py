"""Laps for runs and rides, and lengths for pool swims, made small.

Garmin's split data for one swim is a few hundred fields per length; for a run
it includes GPS coordinates per lap. A model needs neither. This keeps the
numbers that answer "how did my pace hold up": per lap, and for pool swims per
100 of the pool's own unit (yards in a yard pool), plus a first-half against
second-half comparison and the fastest and slowest stretch.
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from .common import ApiModel

YARD_M = 0.9144


def pace_text(seconds: float | None, per: str) -> str | None:
    """1:46/100yd, 5:29/km."""
    if seconds is None:
        return None
    whole = round(seconds)
    minutes, secs = divmod(whole, 60)
    return f"{minutes}:{secs:02d}/{per}"


class Lap(ApiModel):
    # Swim and run fields are set after construction; checked as they land.
    model_config = ConfigDict(populate_by_name=True, extra="ignore", validate_assignment=True)

    lap: int
    distance_m: float | None = None
    duration_s: float | None = None
    moving_s: float | None = None
    pace: str | None = Field(default=None, description="Per km for runs, per 100 for swims.")
    speed_kph: float | None = Field(default=None, description="For rides.")
    average_hr: float | None = None
    max_hr: float | None = None
    cadence: float | None = None
    power_w: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    strokes: int | None = None
    swolf: float | None = None
    stroke: str | None = None
    lengths: int | None = None


class SwimSegment(ApiModel):
    """One stretch of a pool swim, normally 100 of the pool's unit."""

    from_length: int
    to_length: int
    distance: float = Field(description="In the pool's unit.")
    duration_s: float
    pace: str | None = None
    strokes: int | None = None
    swolf: float | None = None
    stroke: str | None = None


class ActivityLaps(ApiModel):
    activity_id: int
    sport: str | None = None
    pool_length: float | None = Field(default=None, description="In pool_unit.")
    pool_unit: str | None = Field(default=None, description='"m" or "yd".')
    laps: list[Lap] = Field(default_factory=list)
    segments: list[SwimSegment] | None = Field(
        default=None, description="Pool swims: pace per 100 of the pool's unit."
    )
    summary: dict[str, Any] = Field(default_factory=dict)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value: float | None, places: int = 1) -> float | None:
    return round(value, places) if value is not None else None


def build(
    activity_id: int,
    sport: str | None,
    splits: dict[str, Any],
    *,
    pool_length: float | None = None,
    pool_unit: str | None = None,
) -> ActivityLaps:
    """Shape Garmin's split payload for one activity."""
    kind = _kind(sport, splits)
    raw_laps = [lap for lap in splits.get("lapDTOs") or [] if isinstance(lap, dict)]
    unit_m = YARD_M if pool_unit == "yd" else 1.0
    per = f"100{pool_unit or 'm'}"

    laps: list[Lap] = []
    for i, raw in enumerate(raw_laps, start=1):
        distance = _num(raw.get("distance"))
        duration = _num(raw.get("duration"))
        speed = _num(raw.get("averageSpeed"))
        lap = Lap(
            lap=int(raw.get("lapIndex") or i),
            distance_m=_round(distance),
            duration_s=_round(duration),
            moving_s=_round(_num(raw.get("movingDuration"))),
            average_hr=_num(raw.get("averageHR")),
            max_hr=_num(raw.get("maxHR")),
            power_w=_round(_num(raw.get("averagePower")), 0),
            elevation_gain_m=_round(_num(raw.get("elevationGain"))),
            elevation_loss_m=_round(_num(raw.get("elevationLoss"))),
        )
        if kind == "swim":
            lap.strokes = raw.get("totalNumberOfStrokes")
            lap.swolf = _num(raw.get("averageSWOLF"))
            lap.stroke = raw.get("swimStroke")
            lap.lengths = raw.get("numberOfActiveLengths")
            if distance and duration:
                lap.pace = pace_text(duration / (distance / unit_m) * 100, per)
        elif kind == "run":
            lap.cadence = _round(_num(raw.get("averageRunCadence")), 0)
            if distance and duration:
                lap.pace = pace_text(duration / distance * 1000, "km")
        else:
            lap.cadence = _round(_num(raw.get("averageBikeCadence")), 0)
            if speed is not None:
                lap.speed_kph = round(speed * 3.6, 1)
        laps.append(lap)

    result = ActivityLaps(
        activity_id=activity_id,
        sport=sport,
        pool_length=_round(pool_length, 2),
        pool_unit=pool_unit,
        laps=laps,
    )
    if kind == "swim":
        result.segments, result.summary = _swim(raw_laps, pool_length, unit_m, per)
    else:
        result.summary = _laps_summary(laps, kind)
    return result


def _kind(sport: str | None, splits: dict[str, Any]) -> str:
    key = (sport or "").lower()
    if "swim" in key or any((lap or {}).get("lengthDTOs") for lap in splits.get("lapDTOs") or []):
        return "swim"
    if any(word in key for word in ("run", "walk", "hik", "trail")):
        return "run"
    return "ride"


def _swim(
    raw_laps: list[dict[str, Any]], pool_length: float | None, unit_m: float, per: str
) -> tuple[list[SwimSegment], dict[str, Any]]:
    """Pace per 100 of the pool's unit, never spanning a rest between laps."""
    per_segment = max(1, round(100 / pool_length)) if pool_length else 4
    segments: list[SwimSegment] = []
    lengths_all: list[dict[str, Any]] = []
    number = 0
    for raw in raw_laps:
        lengths = [
            x
            for x in raw.get("lengthDTOs") or []
            if _num(x.get("distance")) and _num(x.get("duration"))
        ]
        for i in range(0, len(lengths), per_segment):
            chunk = lengths[i : i + per_segment]
            first_n, number = number + 1, number + len(chunk)
            distance = sum(_num(x["distance"]) or 0 for x in chunk) / unit_m
            duration = sum(_num(x["duration"]) or 0 for x in chunk)
            strokes = [
                x.get("totalNumberOfStrokes")
                for x in chunk
                if x.get("totalNumberOfStrokes") is not None
            ]
            swolfs = [
                _num(x.get("averageSWOLF")) for x in chunk if x.get("averageSWOLF") is not None
            ]
            kinds = {x.get("swimStroke") for x in chunk if x.get("swimStroke")}
            segments.append(
                SwimSegment(
                    from_length=first_n,
                    to_length=number,
                    distance=round(distance, 1),
                    duration_s=round(duration, 1),
                    pace=pace_text(duration / distance * 100, per) if distance else None,
                    strokes=sum(strokes) if strokes else None,
                    swolf=round(sum(s for s in swolfs if s is not None) / len(swolfs), 1)
                    if swolfs
                    else None,
                    stroke=kinds.pop() if len(kinds) == 1 else ("MIXED" if kinds else None),
                )
            )
        lengths_all.extend(lengths)

    summary: dict[str, Any] = {"lengths": len(lengths_all)}
    if lengths_all:
        distance = sum(_num(x["distance"]) or 0 for x in lengths_all) / unit_m
        duration = sum(_num(x["duration"]) or 0 for x in lengths_all)
        summary["distance"] = round(distance, 1)
        summary["swim_time_s"] = round(duration, 1)
        summary["average_pace"] = pace_text(duration / distance * 100, per)
        half = len(lengths_all) // 2
        if half:
            summary["first_half_pace"] = _pace_of(lengths_all[:half], unit_m, per)
            summary["second_half_pace"] = _pace_of(lengths_all[half:], unit_m, per)
        by_stroke: dict[str, float] = {}
        for x in lengths_all:
            name = x.get("swimStroke") or "UNKNOWN"
            by_stroke[name] = by_stroke.get(name, 0.0) + (_num(x["distance"]) or 0) / unit_m
        summary["by_stroke"] = {k: round(v, 1) for k, v in by_stroke.items()}
    full = [s for s in segments if s.to_length - s.from_length + 1 == per_segment]
    if full:
        fastest = min(full, key=lambda s: s.duration_s)
        slowest = max(full, key=lambda s: s.duration_s)
        summary["fastest_segment"] = {
            "lengths": f"{fastest.from_length}-{fastest.to_length}",
            "pace": fastest.pace,
        }
        summary["slowest_segment"] = {
            "lengths": f"{slowest.from_length}-{slowest.to_length}",
            "pace": slowest.pace,
        }
    return segments, summary


def _pace_of(lengths: list[dict[str, Any]], unit_m: float, per: str) -> str | None:
    distance = sum(_num(x["distance"]) or 0 for x in lengths) / unit_m
    duration = sum(_num(x["duration"]) or 0 for x in lengths)
    return pace_text(duration / distance * 100, per) if distance else None


def _laps_summary(laps: list[Lap], kind: str) -> dict[str, Any]:
    """Totals and halves on moving time, as Garmin's own averages are.

    Elapsed time counts every stop at a light, which drags a ride's average
    well below what Garmin shows for the same ride.
    """

    def moving(lap: Lap) -> float:
        return lap.moving_s or lap.duration_s or 0.0

    timed = [lap for lap in laps if lap.distance_m and moving(lap)]
    if not timed:
        return {"laps": len(laps)}
    summary: dict[str, Any] = {
        "laps": len(laps),
        "distance_m": round(sum(lap.distance_m or 0 for lap in timed), 1),
        "moving_time_s": round(sum(moving(lap) for lap in timed), 1),
        "elapsed_time_s": round(sum(lap.duration_s or 0 for lap in timed), 1),
    }

    def rate(group: list[Lap]) -> str | float | None:
        d = sum(lap.distance_m or 0 for lap in group)
        t = sum(moving(lap) for lap in group)
        if not d or not t:
            return None
        return pace_text(t / d * 1000, "km") if kind == "run" else round(d / t * 3.6, 1)

    key = "pace" if kind == "run" else "speed_kph"
    summary[f"average_{key}"] = rate(timed)
    if len(timed) >= 2:
        half = len(timed) // 2
        summary[f"first_half_{key}"] = rate(timed[:half])
        summary[f"second_half_{key}"] = rate(timed[half:])
        by_rate = sorted(timed, key=lambda lap: moving(lap) / (lap.distance_m or 1))
        summary["fastest_lap"] = by_rate[0].lap
        summary["slowest_lap"] = by_rate[-1].lap
    return summary
