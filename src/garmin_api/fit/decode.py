"""Reading a strength activity back out of a FIT file.

The mirror of :mod:`garmin_api.fit.encode`, and the way to get a Garmin
workout into another system: download the original file for an activity, then
decode it here into the same typed shape used to write one.

Garmin's "Export Original" hands back a zip, so a zip containing exactly one
FIT file is unwrapped automatically.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile.profile_type import SetType
from pydantic import ValidationError

from ..errors import InvalidRequest
from ..models.workouts import (
    StrengthSessionExercise,
    StrengthSessionSet,
    StrengthSessionSpec,
)
from . import exercises as fx


def _unwrap(data: bytes) -> bytes:
    """Return FIT bytes, unwrapping a single-entry zip if that is what came."""
    if not data:
        raise InvalidRequest("The file is empty.")
    if data[:2] != b"PK":
        return data
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".fit")]
            if len(names) != 1:
                raise InvalidRequest(
                    f"Expected exactly one .fit file in the archive, found {len(names)}."
                )
            return archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise InvalidRequest("The zip archive is damaged.", detail=str(exc)) from exc


def parse_fit(data: bytes) -> FitFile:
    """Parse FIT bytes, or say plainly that they are not a FIT file.

    fit-tool raises a variety of exceptions on malformed input, from its own
    to IndexError and struct errors; all of them mean the same thing here.
    """
    if len(data) < 12 or data[8:12] != b".FIT":
        raise InvalidRequest(
            "This is not a FIT file.", detail="A FIT file starts with a .FIT header."
        )
    try:
        return FitFile.from_bytes(data)
    except Exception as exc:  # noqa: BLE001 -- any parse failure is a bad file
        raise InvalidRequest("The FIT file could not be read.", detail=str(exc)) from exc


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def field_value(message: Any, name: str) -> Any:
    """A field's value, or None where the file holds FIT's "no value" marker.

    fit-tool decodes that marker like any other number. A watch writes it for
    the weight of a bodyweight set and the reps of a timed one, which would
    otherwise read as 4,095.94 kg and 65,535 reps.
    """
    field = message.get_field_by_name(name)
    if field is None or not field.encoded_values:
        return None
    raw = field.encoded_values[0]
    if raw is None or raw == field.base_type.invalid_raw_value():
        return None
    return getattr(message, name)


def decode_strength_session(data: bytes, *, name: str = "Strength Training") -> StrengthSessionSpec:
    """Decode FIT bytes into a strength session; values out of range are the file's fault."""
    try:
        return _decode(data, name)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise InvalidRequest("The FIT file holds values out of range.", detail=problems) from exc


def _decode(data: bytes, name: str) -> StrengthSessionSpec:
    """Decode FIT bytes into a strength session.

    Consecutive sets of the same exercise are grouped back into one exercise,
    and rest messages are folded into the ``rest_s`` of the set they follow,
    undoing exactly what the encoder did.
    """
    fit = parse_fit(_unwrap(data))
    messages = [record.message for record in fit.records]

    # The session message comes after the sets, in Garmin's files and in ours,
    # and its start is the session's own; the first set may begin later.
    start = next(
        (
            _dt(field_value(m, "start_time"))
            for m in messages
            if isinstance(m, SessionMessage) and field_value(m, "start_time") is not None
        ),
        None,
    )
    zone = _zone(messages)
    exercises: list[StrengthSessionExercise] = []
    last_working: StrengthSessionSet | None = None
    last_end: float | None = None
    awaiting_rest = False

    for message in messages:
        if not isinstance(message, SetMessage):
            continue

        # Decoded fields come back as the raw enum value, not the enum member,
        # so compare on the value: 0 is REST, 1 is ACTIVE.
        set_type = message.set_type
        value = set_type.value if hasattr(set_type, "value") else set_type

        if value == SetType.REST.value:
            # A rest belongs to the set before it.
            duration = field_value(message, "duration")
            if last_working is not None and duration is not None:
                last_working.rest_s = float(duration)
            awaiting_rest = False
            continue

        category_value = _first_value(message.category)
        subtype_value = _first_value(message.category_subtype)
        if subtype_value == 0xFFFF:
            subtype_value = None
        starts = field_value(message, "start_time")

        if last_working is not None and awaiting_rest:
            # No rest message: the time between the sets, if the file says, or
            # none. Not an unrecorded rest, which re-encoding would fill in.
            gap = (starts - last_end) / 1000 if starts is not None and last_end is not None else 0
            last_working.rest_s = round(max(gap, 0.0), 3)

        # A set the watch could not identify is kept, as UNKNOWN, rather than
        # dropped: dropping it lost the set and gave its rest to the one before.
        known = category_value is not None and category_value != 0xFFFF
        category = (fx.category_name(int(category_value)) if known else None) or "UNKNOWN"
        exercise_name = (
            fx.exercise_name(int(category_value), subtype_value) if known else ""
        ) or ""

        reps = field_value(message, "repetitions")
        weight = field_value(message, "weight")
        duration = field_value(message, "duration")
        entry = StrengthSessionSet(
            reps=int(reps) if reps is not None else None,
            weight_kg=float(weight) if weight is not None else None,
            duration_s=float(duration) if duration is not None else None,
        )
        last_working = entry
        awaiting_rest = True
        last_end = (
            starts + entry.duration_s * 1000
            if starts is not None and entry.duration_s is not None
            else None
        )

        if (
            exercises
            and exercises[-1].category == category
            and exercises[-1].exercise_name == (exercise_name or category)
        ):
            exercises[-1].sets.append(entry)
        else:
            exercises.append(
                StrengthSessionExercise(
                    category=category, exercise_name=exercise_name, sets=[entry]
                )
            )

        if start is None:
            start = _dt(field_value(message, "start_time"))

    if not exercises:
        raise InvalidRequest(
            "No strength sets found in this FIT file.",
            detail="Only strength activities carry set messages; a run or ride has none.",
        )

    started = start or datetime.now(tz=UTC)
    return StrengthSessionSpec(
        name=name,
        # In the session's own timezone, so encoding it again keeps the local time.
        start_time=started.astimezone(zone) if zone else started,
        exercises=exercises,
    )


def _zone(messages: list[Any]) -> timezone | None:
    """The file's UTC offset: the activity's local timestamp minus its UTC one."""
    from fit_tool.profile.messages.activity_message import ActivityMessage

    from .encode import FIT_EPOCH_OFFSET

    for message in messages:
        if not isinstance(message, ActivityMessage):
            continue
        local, stamp = field_value(message, "local_timestamp"), field_value(message, "timestamp")
        if local is None or stamp is None:
            return None
        # Rounded to the minute: offsets are whole minutes, and older files
        # could be a second out.
        offset = round((local - (stamp / 1000 - FIT_EPOCH_OFFSET)) / 60) * 60
        return timezone(timedelta(seconds=offset)) if abs(offset) <= 14 * 3600 else None
    return None


def _first_value(value: Any) -> Any:
    """FIT array fields decode as lists; take the first entry."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value
