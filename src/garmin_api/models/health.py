"""Daily health data.

Garmin spreads one day across a dozen endpoints with overlapping and
contradictory field names. :class:`DaySummary` is the assembled answer to the
question people actually ask, "how was Tuesday", with each part optional
because any one endpoint can return nothing for a day the watch was off.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .common import ApiModel, WriteSpec, first, parse_date, parse_dt, within_fit_years


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
            # GMT only: the Local fields are wall-clock times dressed as epochs,
            # and read as instants they land hours out.
            start=parse_dt(first(daily, "sleepStartTimestampGMT")),
            end=parse_dt(first(daily, "sleepEndTimestampGMT")),
            total_sleep_s=first(daily, "sleepTimeSeconds"),
            deep_s=first(daily, "deepSleepSeconds"),
            light_s=first(daily, "lightSleepSeconds"),
            rem_s=first(daily, "remSleepSeconds"),
            awake_s=first(daily, "awakeSleepSeconds"),
            sleep_score=overall.get("value") if isinstance(overall, dict) else None,
            # Both live in dailySleepDTO; the top level is a fallback.
            average_spo2=first(daily, "averageSpO2Value", "avgSpO2", "averageSpO2")
            or first(payload, "averageSpO2Value", "avgSpO2", "averageSpO2"),
            average_respiration=first(daily, "averageRespirationValue", "avgRespirationValue")
            or first(payload, "averageRespirationValue", "avgRespirationValue"),
            raw=payload if include_raw else None,
        )


# Garmin hands metabolic age back as a duration in milliseconds: 24 years
# arrives as 757366224000. Captured live by writing ages 24, 30 and 50 and
# reading them back, which divided out to exactly 24.0, 30.0 and 50.0.
MS_PER_YEAR = 365.2425 * 86_400_000


def _metabolic_age_years(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # A plain count of years is left alone, in case Garmin ever sends one.
    return round(number / MS_PER_YEAR) if number > 1000 else round(number)


class WeighIn(ApiModel):
    sample_pk: int | None = Field(
        default=None, description="Garmin's id for this weigh-in, which delete takes."
    )
    day: date | None = None
    timestamp: datetime | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat_pct: float | None = None
    body_water_pct: float | None = None
    muscle_mass_kg: float | None = None
    bone_mass_kg: float | None = None
    visceral_fat_rating: int | None = None
    metabolic_age: int | None = None
    physique_rating: int | None = None
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
            sample_pk=payload.get("samplePk"),
            day=parse_date(first(payload, "calendarDate", "date")),
            timestamp=parse_dt(first(payload, "timestampGMT", "date")),
            weight_kg=g_to_kg(first(payload, "weight")),
            bmi=first(payload, "bmi"),
            body_fat_pct=first(payload, "bodyFat"),
            body_water_pct=first(payload, "bodyWater"),
            muscle_mass_kg=g_to_kg(first(payload, "muscleMass")),
            bone_mass_kg=g_to_kg(first(payload, "boneMass")),
            visceral_fat_rating=first(payload, "visceralFat"),
            metabolic_age=_metabolic_age_years(first(payload, "metabolicAge")),
            physique_rating=first(payload, "physiqueRating"),
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

    # Parts are filled in one by one after construction; each is checked as
    # it lands, so a reply Garmin shaped oddly cannot break the output schema.
    model_config = ConfigDict(populate_by_name=True, extra="ignore", validate_assignment=True)

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
    hrv_last_night_avg: float | None = None
    hrv_status: str | None = None
    training_readiness: int | None = Field(
        default=None, description="The morning score Garmin sets on waking."
    )
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
            average_stress=_reading(first(stats, "averageStressLevel")),
            body_battery_high=first(stats, "bodyBatteryHighestValue"),
            body_battery_low=first(stats, "bodyBatteryLowestValue"),
            raw=stats if include_raw else None,
        )


class HrvSummary(ApiModel):
    """One night's heart rate variability, without the five-minute readings."""

    day: date | None = None
    # Milliseconds. Garmin's own typed model allows fractions.
    last_night_avg: float | None = None
    last_night_5min_high: float | None = None
    weekly_avg: float | None = None
    status: str | None = Field(default=None, description='e.g. "BALANCED", "LOW", "UNBALANCED".')
    baseline_low_upper: float | None = None
    baseline_balanced_low: float | None = None
    baseline_balanced_upper: float | None = None
    readings: list[dict[str, Any]] | None = Field(
        default=None, description="The five-minute readings, only when asked for."
    )

    @classmethod
    def from_garmin(cls, payload: dict[str, Any], *, include_readings: bool = False) -> HrvSummary:
        summary = payload.get("hrvSummary") or {}
        baseline = summary.get("baseline") or {}
        return cls(
            day=parse_date(summary.get("calendarDate")),
            last_night_avg=summary.get("lastNightAvg"),
            last_night_5min_high=summary.get("lastNight5MinHigh"),
            weekly_avg=summary.get("weeklyAvg"),
            status=summary.get("status"),
            baseline_low_upper=baseline.get("lowUpper"),
            baseline_balanced_low=baseline.get("balancedLow"),
            baseline_balanced_upper=baseline.get("balancedUpper"),
            readings=(payload.get("hrvReadings") or []) if include_readings else None,
        )


# Garmin's published stress bands. A reading of -1 or -2 is Garmin's code for
# "no reading": the watch was off the wrist, or there was too much movement.
STRESS_BANDS = (("rest", 0, 25), ("low", 26, 50), ("medium", 51, 75), ("high", 76, 100))


def _reading(value: Any) -> Any:
    """A stress level, or None for Garmin's negative codes: -1 and -2 mean no reading."""
    return None if isinstance(value, (int, float)) and value < 0 else value


class StressSummary(ApiModel):
    """A day of stress, as minutes in each of Garmin's bands.

    Garmin samples every three minutes, so a day is 480 readings. Those are
    reduced to what the Garmin app itself shows: an average, a peak, and how
    long was spent resting, low, medium and high.
    """

    day: date | None = None
    average: int | None = None
    max: int | None = None
    # None when the day has no samples to count.
    rest_minutes: int | None = 0
    low_minutes: int | None = 0
    medium_minutes: int | None = 0
    high_minutes: int | None = 0
    unmeasured_minutes: int | None = Field(
        default=0, description="Off the wrist, or too much movement to read."
    )
    samples: list[list[Any]] | None = Field(
        default=None, description="[timestamp_ms, level] pairs, only when asked for."
    )

    @classmethod
    def from_garmin(
        cls, payload: dict[str, Any], *, include_samples: bool = False
    ) -> StressSummary:
        values = [v for v in (payload.get("stressValuesArray") or []) if isinstance(v, list)]
        times = [v[0] for v in values]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False) if b > a]
        interval_min = (min(gaps) / 60000) if gaps else 3.0

        minutes = {name: 0.0 for name, _, _ in STRESS_BANDS}
        unmeasured = 0.0
        for _, level in values:
            if level is None or level < 0:
                unmeasured += interval_min
                continue
            for name, low, high in STRESS_BANDS:
                if low <= level <= high:
                    minutes[name] += interval_min
                    break

        counted = bool(values)
        return cls(
            day=parse_date(payload.get("calendarDate")),
            average=_reading(payload.get("avgStressLevel")),
            max=_reading(payload.get("maxStressLevel")),
            rest_minutes=round(minutes["rest"]) if counted else None,
            low_minutes=round(minutes["low"]) if counted else None,
            medium_minutes=round(minutes["medium"]) if counted else None,
            high_minutes=round(minutes["high"]) if counted else None,
            unmeasured_minutes=round(unmeasured) if counted else None,
            samples=values if include_samples else None,
        )


class StepsSummary(ApiModel):
    """A day of steps: the total and an hour-by-hour profile.

    Garmin reports quarter-hour buckets in GMT; they are summed into 24 hourly
    figures, also in GMT, with the bucket times available on request.
    """

    day: date
    total: int | None = Field(default=0, description="None when Garmin did not answer.")
    hourly_gmt: list[int] = Field(default_factory=list, description="24 values, hour 0 to 23 GMT.")
    buckets: list[dict[str, Any]] | None = Field(
        default=None, description="Garmin's quarter-hour buckets, only when asked for."
    )

    @classmethod
    def from_garmin(
        cls, day: date, payload: list[dict[str, Any]], *, include_buckets: bool = False
    ) -> StepsSummary:
        hourly = [0] * 24
        total = 0
        for bucket in payload or []:
            count = int(bucket.get("steps") or 0)
            total += count
            start = parse_dt(bucket.get("startGMT"))
            if start is not None:
                hourly[start.hour] += count
        return cls(
            day=day,
            total=total,
            hourly_gmt=hourly,
            buckets=list(payload or []) if include_buckets else None,
        )


class DeviceSummary(ApiModel):
    device_id: int
    name: str | None = None
    product: str | None = None
    part_number: str | None = None
    primary: bool = Field(default=False, description="The primary activity tracker.")
    status: str | None = None

    @classmethod
    def from_garmin(cls, payload: dict[str, Any]) -> DeviceSummary:
        return cls(
            device_id=int(payload.get("deviceId") or 0),
            name=payload.get("displayName"),
            product=payload.get("productDisplayName"),
            part_number=payload.get("partNumber"),
            primary=bool(payload.get("primaryActivityTrackerIndicator")),
            status=payload.get("deviceStatus"),
        )


class PersonalRecord(ApiModel):
    """One personal record, named and in units.

    ``value`` is in ``unit``: seconds for a fastest distance, metres for a
    longest distance or climb, watts, steps, days, or kilograms. ``display``
    is the same thing as a person reads it: 5:14.8, 3.05 km, 326 days.
    """

    record_id: int | None = None
    type_id: int | None = None
    label: str | None = Field(default=None, description='e.g. "Fastest 1 km run".')
    sport: str | None = None
    value: float | None = None
    unit: str | None = None
    display: str | None = None
    activity_type: str | None = None
    activity_id: int | None = None
    activity_name: str | None = None
    achieved_at: datetime | None = None

    @classmethod
    def from_garmin(
        cls, payload: dict[str, Any], types: dict[int, dict[str, Any]] | None = None
    ) -> PersonalRecord:
        from .records import convert, display

        type_id = payload.get("typeId")
        info = (types or {}).get(int(type_id)) if type_id is not None else None
        unit = info["unit"] if info else None
        value = convert(payload.get("value"), unit) if unit else payload.get("value")
        return cls(
            record_id=payload.get("id"),
            type_id=type_id,
            label=info["label"] if info else None,
            sport=info["sport"] if info else None,
            value=value,
            unit=unit,
            display=display(value, unit) if unit else None,
            activity_type=payload.get("activityType"),
            activity_id=payload.get("activityId"),
            activity_name=payload.get("activityName"),
            achieved_at=parse_dt(
                first(payload, "prStartTimeGmtFormatted", "actStartDateTimeInGMTFormatted")
            ),
        )


class BodyCompositionSpec(WriteSpec):
    """One weigh-in with body composition, to write to Garmin.

    Written as a FIT weight-scale file, which is how Garmin's own Index scales
    deliver theirs, and the only route by which body fat, muscle and the rest
    reach Garmin Connect. A plain weigh-in carries weight alone.
    """

    timestamp: datetime = Field(
        description=(
            "When it was measured. Without an offset, it is taken as local time in the Garmin "
            "account's own timezone."
        )
    )
    weight_kg: float = Field(gt=0, le=650)
    percent_fat: float | None = Field(default=None, ge=0, le=100)
    percent_hydration: float | None = Field(default=None, ge=0, le=100)
    visceral_fat_mass_kg: float | None = Field(default=None, ge=0, le=650)
    bone_mass_kg: float | None = Field(default=None, ge=0, le=650)
    muscle_mass_kg: float | None = Field(default=None, ge=0, le=650)
    basal_met_kcal: float | None = Field(default=None, ge=0, le=16000)
    active_met_kcal: float | None = Field(default=None, ge=0, le=16000)
    physique_rating: int | None = Field(default=None, ge=1, le=9)
    metabolic_age: int | None = Field(default=None, ge=1, le=120)
    visceral_fat_rating: int | None = Field(default=None, ge=1, le=59)
    bmi: float | None = Field(default=None, ge=0, le=200)

    @field_validator("timestamp")
    @classmethod
    def _in_range(cls, value: datetime) -> datetime:
        # Placed in the account's timezone when recorded, if it has no offset.
        return within_fit_years(value)
