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
from datetime import UTC, datetime
from typing import Any

from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile.profile_type import SetType

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
    exercises: list[StrengthSessionExercise] = []
    last_working: StrengthSessionSet | None = None
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
            if last_working is not None and duration:
                last_working.rest_s = float(duration)
            awaiting_rest = False
            continue

        category_value = _first_value(message.category)
        subtype_value = _first_value(message.category_subtype)
        if category_value is None or category_value == 0xFFFF:
            continue
        if subtype_value == 0xFFFF:
            subtype_value = None

        if last_working is not None and awaiting_rest:
            # Straight into the next set: no rest, which is not the same as
            # an unrecorded one, and re-encoding must not invent one.
            last_working.rest_s = 0.0

        category = fx.category_name(int(category_value)) or "UNKNOWN"
        exercise_name = fx.exercise_name(int(category_value), subtype_value) or ""

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

    return StrengthSessionSpec(
        name=name,
        start_time=start or datetime.now(tz=UTC),
        exercises=exercises,
    )


def _first_value(value: Any) -> Any:
    """FIT array fields decode as lists; take the first entry."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value
