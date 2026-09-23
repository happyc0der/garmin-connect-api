"""Daily health data.

Garmin spreads one day across a dozen endpoints with overlapping and
contradictory field names. :class:`DaySummary` is the assembled answer to the
question people actually ask, "how was Tuesday", with each part optional
because any one endpoint can return nothing for a day the watch was off.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import Field

from .common import ApiModel, first, parse_date, parse_dt


class SleepSummary(ApiModel):
    day: date | None = None
    start: datetime | None = None
    end: datetime | None = None
    total_sleep_s: int | None = None
    deep_s: int | None = None
    light_s: int | None = None
    rem_s: int | None = None
    awake_s: int | None = None
    sleep_score: int | None = None
    average_spo2: float | None = None
    average_respiration: float | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_raw: bool = False) -> SleepSummary:
        daily = payload.get("dailySleepDTO") or payload
        scores = daily.get("sleepScores") or {}
        overall = scores.get("overall") or {}
        return cls(
            day=parse_date(first(daily, "calendarDate", "calendar_date")),
            start=parse_dt(first(daily, "sleepStartTimestampGMT", "sleepStartTimestampLocal")),
            end=parse_dt(first(daily, "sleepEndTimestampGMT", "sleepEndTimestampLocal")),
            total_sleep_s=first(daily, "sleepTimeSeconds"),
            deep_s=first(daily, "deepSleepSeconds"),
            light_s=first(daily, "lightSleepSeconds"),
            rem_s=first(daily, "remSleepSeconds"),
            awake_s=first(daily, "awakeSleepSeconds"),
            sleep_score=overall.get("value") if isinstance(overall, dict) else None,
            average_spo2=first(payload, "averageSpO2Value", "averageSpO2"),
            average_respiration=first(payload, "avgRespirationValue", "averageRespirationValue"),
            raw=payload if include_raw else None,
        )


class WeighIn(ApiModel):
    day: date | None = None
    timestamp: datetime | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat_pct: float | None = None
    body_water_pct: float | None = None
    muscle_mass_kg: float | None = None
    bone_mass_kg: float | None = None
    source: str | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_raw: bool = False) -> WeighIn:
        # Garmin stores body weights in grams here, unlike the kilograms it
        # accepts when writing one back.
        def g_to_kg(value: Any) -> float | None:
            try:
                return round(float(value) / 1000.0, 2) if value is not None else None
            except (TypeError, ValueError):
                return None

        return cls(
            day=parse_date(first(payload, "calendarDate", "date")),
            timestamp=parse_dt(first(payload, "timestampGMT", "date")),
            weight_kg=g_to_kg(first(payload, "weight")),
            bmi=first(payload, "bmi"),
            body_fat_pct=first(payload, "bodyFat"),
            body_water_pct=first(payload, "bodyWater"),
            muscle_mass_kg=g_to_kg(first(payload, "muscleMass")),
            bone_mass_kg=g_to_kg(first(payload, "boneMass")),
            source=first(payload, "sourceType"),
            raw=payload if include_raw else None,
        )


class BodyComposition(ApiModel):
    day: date | None = None
    entries: list[WeighIn] = Field(default_factory=list)


class DaySummary(ApiModel):
    """One day, assembled from several Garmin endpoints.

    Any field can be ``None``: the watch may not have been worn, the metric may
    not exist on that model, or Garmin may simply not have computed it yet.
    """

    day: date
    steps: int | None = None
    step_goal: int | None = None
    distance_m: float | None = None
    floors_climbed: float | None = Field(
        default=None, description="Garmin reports floors fractionally, e.g. 2.25."
    )
    active_calories: float | None = None
    total_calories: float | None = None
    moderate_intensity_minutes: int | None = None
    vigorous_intensity_minutes: int | None = Field(
        default=None, description="Garmin counts these double toward the weekly goal."
    )
    resting_hr: int | None = None
    min_hr: int | None = None
    max_hr: int | None = None
    average_stress: int | None = None
    body_battery_high: int | None = None
    body_battery_low: int | None = None
    hrv_last_night_avg: int | None = None
    hrv_status: str | None = None
    training_readiness: int | None = None
    sleep: SleepSummary | None = None
    weight: WeighIn | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_stats(
        cls, day: date, stats: dict[str, Any], *, include_raw: bool = False
    ) -> DaySummary:
        return cls(
            day=day,
            steps=first(stats, "totalSteps", "steps"),
            step_goal=first(stats, "dailyStepGoal", "stepGoal"),
            distance_m=first(stats, "totalDistanceMeters", "distance"),
            floors_climbed=first(stats, "floorsAscended", "floorsClimbed"),
            active_calories=first(stats, "activeKilocalories"),
            total_calories=first(stats, "totalKilocalories"),
            moderate_intensity_minutes=first(stats, "moderateIntensityMinutes"),
            vigorous_intensity_minutes=first(stats, "vigorousIntensityMinutes"),
            resting_hr=first(stats, "restingHeartRate"),
            min_hr=first(stats, "minHeartRate"),
            max_hr=first(stats, "maxHeartRate"),
            average_stress=first(stats, "averageStressLevel"),
            body_battery_high=first(stats, "bodyBatteryHighestValue"),
            body_battery_low=first(stats, "bodyBatteryLowestValue"),
            raw=stats if include_raw else None,
        )
