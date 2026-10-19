"""Body weight and composition."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .. import auth
from ..models.common import as_date_str
from ..models.health import BodyCompositionSpec, WeighIn


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


def add_body_composition(spec: BodyCompositionSpec) -> dict[str, Any]:
    """Record a weigh-in with body composition: fat, water, muscle, bone and more.

    Uploaded as a FIT weight-scale file, the same way Garmin's own scales send
    theirs. The file's serial comes from the measurement, so recording the same
    weigh-in twice is reported as a duplicate rather than stored twice.
    """
    import tempfile
    from pathlib import Path

    from ..fit.body import encode_body_composition
    from .uploads import upload_file

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "body_composition.fit"
        target.write_bytes(encode_body_composition(spec))
        result = upload_file(target)
    result["timestamp"] = spec.timestamp.isoformat()
    result["weight_kg"] = spec.weight_kg
    return result
