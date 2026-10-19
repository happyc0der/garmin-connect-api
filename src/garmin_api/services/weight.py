"""Body weight and composition."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .. import auth
from ..errors import InvalidRequest
from ..models.common import as_date_str
from ..models.health import BodyCompositionSpec, WeighIn


def weigh_ins(
    start: date | str, end: date | str | None = None, *, include_raw: bool = False
) -> list[WeighIn]:
    """Every weigh-in in a date range, oldest first.

    Uses Garmin's weight-range call with ``includeAll``. The body-composition
    date-range call looks equivalent but returns one weigh-in per day, so a
    day with two readings silently lost one -- found when a sync of 35 smart-scale
    weigh-ins appeared to have landed only 31.
    """
    start_str = as_date_str(start)
    payload = auth.call("get_weigh_ins", start_str, as_date_str(end) if end else start_str)
    entries = [
        entry
        for day in (payload or {}).get("dailyWeightSummaries") or []
        for entry in day.get("allWeightMetrics") or []
    ]
    rows = [WeighIn.from_garmin(e, include_raw=include_raw) for e in entries]
    return sorted(rows, key=lambda w: (w.timestamp is None, w.timestamp))


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
    stamp = ""
    if timestamp:
        try:
            moment = (
                timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(timestamp)
            )
        except ValueError as exc:
            raise InvalidRequest(
                f"Not a timestamp: {timestamp!r}.", detail="Use ISO 8601."
            ) from exc
        if moment.tzinfo is not None:
            # Garmin files a weigh-in under the local date and time it is
            # given, so an instant in UTC would land on the wrong evening's
            # day; it is moved into the account's own timezone first.
            moment = moment.astimezone(auth.call("_user_tzinfo"))
        stamp = moment.isoformat()
    return auth.call("add_weigh_in", weight, unit, stamp)


def delete_weigh_in(weight_pk: str, day: date | str) -> Any:
    return auth.call("delete_weigh_in", weight_pk, as_date_str(day))


def add_body_composition(spec: BodyCompositionSpec) -> dict[str, Any]:
    """Record a weigh-in with body composition: fat, water, muscle, bone and more.

    Uploaded as a FIT weight-scale file, the same way Garmin's own scales send
    theirs. Garmin answers every such upload with "processed" and never says
    whether it was new, but it keeps one entry per identical measurement: the
    same weigh-in sent three times, even as different files, is stored once.
    The same moment with different values is stored as a second entry.
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
