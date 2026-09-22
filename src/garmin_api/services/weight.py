"""Body weight and composition."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .. import auth
from ..models.common import as_date_str
from ..models.health import WeighIn


def weigh_ins(
    start: date | str, end: date | str | None = None, *, include_raw: bool = False
) -> list[WeighIn]:
    payload = auth.call(
        "get_body_composition", as_date_str(start), as_date_str(end) if end else None
    )
    rows = (payload or {}).get("dateWeightList") or []
    return [WeighIn.from_garmin(r, include_raw=include_raw) for r in rows]


def day_weigh_ins(day: date | str, *, include_raw: bool = False) -> list[WeighIn]:
    payload = auth.call("get_daily_weigh_ins", as_date_str(day))
    rows = (payload or {}).get("dateWeightList") or []
    return [WeighIn.from_garmin(r, include_raw=include_raw) for r in rows]


def add_weigh_in(
    weight: float, *, unit: str = "kg", timestamp: datetime | str | None = None
) -> Any:
    """Record a body weight.

    Unlike everywhere else in Garmin's API, this one takes kilograms (or
    pounds) rather than grams.
    """
    stamp = timestamp.isoformat() if isinstance(timestamp, datetime) else (timestamp or "")
    return auth.call("add_weigh_in", weight, unit, stamp)


def delete_weigh_in(weight_pk: str, day: date | str) -> Any:
    return auth.call("delete_weigh_in", weight_pk, as_date_str(day))
