"""Daily health and wellness data.

Garmin splits one day across many endpoints. Each is exposed on its own for
callers who want just that, and :func:`day` assembles the common ones into a
single answer. Any part can come back empty for a day the watch was not worn,
which is reported as ``None`` rather than as an error.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .. import auth
from ..errors import NotAuthenticated, RateLimited
from ..models.common import as_date_str
from ..models.health import (
    DaySummary,
    HrvSummary,
    PersonalRecord,
    SleepSummary,
    StepsSummary,
    StressSummary,
    WeighIn,
)

logger = logging.getLogger(__name__)


def _safe(fn_name: str, *args: Any) -> Any:
    """Call an endpoint, treating "nothing recorded" as None rather than failure.

    A day with no sleep data is a normal answer, not a fault, and one missing
    metric must not sink a whole day summary.

    Tolerance stops at the account itself. Not being logged in, and above all
    being rate limited, are raised: swallowing a 429 here would turn one day
    summary into five more calls against an account Garmin is already
    throttling, which is how an account gets locked out.
    """
    try:
        return auth.call(fn_name, *args)
    except (NotAuthenticated, RateLimited):
        raise
    except Exception as exc:  # noqa: BLE001 -- deliberately tolerant
        logger.debug("%s failed, treating as no data: %s", fn_name, exc)
        return None


def stats(day: date | str) -> Any:
    return auth.call("get_stats", as_date_str(day))


def sleep(day: date | str, *, include_raw: bool = False) -> SleepSummary | None:
    """One night's sleep, or None if none was recorded.

    Garmin answers a night with no data with a full skeleton of nulls, which
    is reported as None rather than as a summary of nothing.
    """
    payload = _safe("get_sleep_data", as_date_str(day))
    if not payload:
        return None
    summary = SleepSummary.from_garmin(payload, include_raw=include_raw)
    if summary.total_sleep_s is None and summary.start is None:
        return None
    return summary


def hrv(day: date | str) -> Any:
    return _safe("get_hrv_data", as_date_str(day))


def resting_hr(day: date | str) -> Any:
    return _safe("get_rhr_day", as_date_str(day))


def stress(day: date | str) -> Any:
    return _safe("get_stress_data", as_date_str(day))


def body_battery(start: date | str, end: date | str | None = None) -> Any:
    return _safe("get_body_battery", as_date_str(start), as_date_str(end) if end else None)


def steps(day: date | str) -> Any:
    return _safe("get_steps_data", as_date_str(day))


def training_readiness(day: date | str) -> Any:
    return _safe("get_training_readiness", as_date_str(day))


def training_status(day: date | str) -> Any:
    return _safe("get_training_status", as_date_str(day))


def heart_rates(day: date | str) -> Any:
    return _safe("get_heart_rates", as_date_str(day))


def max_metrics(day: date | str) -> Any:
    """VO2 max and fitness age, as Garmin computes them."""
    return _safe("get_max_metrics", as_date_str(day))


def personal_records() -> Any:
    return _safe("get_personal_record")


DAY_PARTS = frozenset({"stats", "sleep", "hrv", "readiness", "weight"})


def day(
    target: date | str,
    *,
    include_raw: bool = False,
    parts: frozenset[str] | set[str] = DAY_PARTS,
) -> DaySummary:
    """One day, assembled: activity, heart, stress, sleep, HRV, readiness.

    Several endpoints in one answer, because that is the question people
    actually ask. Each part is fetched tolerantly, so a metric the watch does
    not record leaves a null instead of failing the call.

    Each part is one Garmin request. ``parts`` limits which are fetched, which
    matters when assembling many days: a week of full days is 35 requests.
    """
    unknown = set(parts) - DAY_PARTS
    if unknown:
        raise ValueError(f"Unknown day parts: {sorted(unknown)}")

    day_str = as_date_str(target)
    parsed_day = date.fromisoformat(day_str)
    base = (_safe("get_stats", day_str) or {}) if "stats" in parts else {}
    summary = DaySummary.from_stats(parsed_day, base, include_raw=include_raw)

    if "sleep" in parts:
        summary.sleep = sleep(day_str, include_raw=include_raw)

    if "hrv" in parts:
        hrv_payload = hrv(day_str) or {}
        hrv_part = hrv_payload.get("hrvSummary") if isinstance(hrv_payload, dict) else None
        if isinstance(hrv_part, dict):
            summary.hrv_last_night_avg = hrv_part.get("lastNightAvg")
            summary.hrv_status = hrv_part.get("status")

    if "readiness" in parts:
        readiness = training_readiness(day_str)
        # A list of snapshots, or, on some accounts, a single snapshot object.
        snapshots = readiness if isinstance(readiness, list) else [readiness] if readiness else []
        morning = morning_readiness(snapshots)
        if morning is not None:
            summary.training_readiness = morning.get("score")

    if "weight" in parts:
        weigh_ins = _safe("get_daily_weigh_ins", day_str) or {}
        rows = weigh_ins.get("dateWeightList") if isinstance(weigh_ins, dict) else None
        if rows:
            # The day's last weigh-in, as the weight trend reports it; nothing
            # says what order Garmin lists them in.
            latest = max(rows, key=lambda r: r.get("timestampGMT") or r.get("date") or 0)
            summary.weight = WeighIn.from_garmin(latest, include_raw=include_raw)

    return summary


def morning_readiness(rows: list[Any]) -> dict[str, Any] | None:
    """The day's training readiness entry: the score Garmin sets on waking.

    Garmin keeps several a day, updating the score as the day goes on; the
    one labelled AFTER_WAKEUP_RESET is the morning score, comparable from day
    to day. Without it, the first entry with a score.
    """
    scored = [r for r in rows if isinstance(r, dict) and r.get("score") is not None]
    for row in scored:
        if row.get("inputContext") == "AFTER_WAKEUP_RESET":
            return row
    return scored[0] if scored else None


def hrv_summary(day: date | str, *, include_readings: bool = False) -> HrvSummary | None:
    payload = hrv(day)
    return HrvSummary.from_garmin(payload, include_readings=include_readings) if payload else None


def stress_summary(day: date | str, *, include_samples: bool = False) -> StressSummary | None:
    payload = stress(day)
    return StressSummary.from_garmin(payload, include_samples=include_samples) if payload else None


def steps_summary(day: date | str, *, include_buckets: bool = False) -> StepsSummary:
    day_str = as_date_str(day)
    payload = steps(day_str)
    if not payload:
        # No answer, or an empty one (the library turns both into [] or {}):
        # not the same as no steps, since a worn day has its quarter-hours.
        return StepsSummary(day=date.fromisoformat(day_str), total=None)
    return StepsSummary.from_garmin(
        date.fromisoformat(day_str), payload, include_buckets=include_buckets
    )


_record_types: dict[int, dict[str, Any]] | None = None


def record_types() -> dict[int, dict[str, Any]]:
    """Garmin's record type list, named and given units. Fetched once a process."""
    global _record_types
    if _record_types is None:
        from ..models.records import index

        found = index(_safe("get_personal_record_types"))
        if not found:
            # A failed request; asked again next time rather than leaving
            # every record unnamed for the life of the process.
            return found
        _record_types = found
    return _record_types


def records() -> list[PersonalRecord]:
    """Personal records with names and units, most recent first."""
    types = record_types()
    rows = [PersonalRecord.from_garmin(r, types) for r in (personal_records() or [])]
    # Newest first, and records with no date after all the dated ones.
    return sorted(rows, key=lambda r: (r.achieved_at is not None, r.achieved_at), reverse=True)
