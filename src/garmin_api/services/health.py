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
from ..models.common import as_date_str
from ..models.health import DaySummary, SleepSummary, WeighIn

logger = logging.getLogger(__name__)


def _safe(fn_name: str, *args: Any) -> Any:
    """Call an endpoint, treating "nothing recorded" as None rather than failure.

    A day with no sleep data is a normal answer, not a fault, and one missing
    metric must not sink a whole day summary.
    """
    try:
        return auth.call(fn_name, *args)
    except Exception as exc:  # noqa: BLE001 -- deliberately tolerant
        logger.debug("%s failed, treating as no data: %s", fn_name, exc)
        return None


def stats(day: date | str) -> Any:
    return auth.call("get_stats", as_date_str(day))


def sleep(day: date | str, *, include_raw: bool = False) -> SleepSummary | None:
    payload = _safe("get_sleep_data", as_date_str(day))
    return SleepSummary.from_garmin(payload, include_raw=include_raw) if payload else None


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


def day(target: date | str, *, include_raw: bool = False) -> DaySummary:
    """One day, assembled: activity, heart, stress, sleep, HRV, readiness.

    Several endpoints in one answer, because that is the question people
    actually ask. Each part is fetched tolerantly, so a metric the watch does
    not record leaves a null instead of failing the call.
    """
    day_str = as_date_str(target)
    base = _safe("get_stats", day_str) or {}
    parsed_day = date.fromisoformat(day_str)
    summary = DaySummary.from_stats(parsed_day, base, include_raw=include_raw)

    summary.sleep = sleep(day_str, include_raw=include_raw)

    hrv_payload = hrv(day_str) or {}
    hrv_summary = hrv_payload.get("hrvSummary") if isinstance(hrv_payload, dict) else None
    if isinstance(hrv_summary, dict):
        summary.hrv_last_night_avg = hrv_summary.get("lastNightAvg")
        summary.hrv_status = hrv_summary.get("status")

    readiness = training_readiness(day_str)
    if isinstance(readiness, list) and readiness:
        first_entry = readiness[0]
        if isinstance(first_entry, dict):
            summary.training_readiness = first_entry.get("score")

    weigh_ins = _safe("get_daily_weigh_ins", day_str) or {}
    rows = weigh_ins.get("dateWeightList") if isinstance(weigh_ins, dict) else None
    if rows:
        summary.weight = WeighIn.from_garmin(rows[0], include_raw=include_raw)

    return summary
