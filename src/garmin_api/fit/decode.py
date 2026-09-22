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
    if data[:2] != b"PK":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".fit")]
        if len(names) != 1:
            raise InvalidRequest(
                f"Expected exactly one .fit file in the archive, found {len(names)}."
            )
        return archive.read(names[0])


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def decode_strength_session(data: bytes, *, name: str = "Strength Training") -> StrengthSessionSpec:
    """Decode FIT bytes into a strength session.

    Consecutive sets of the same exercise are grouped back into one exercise,
    and rest messages are folded into the ``rest_s`` of the set they follow,
    undoing exactly what the encoder did.
    """
    fit = FitFile.from_bytes(_unwrap(data))

    start: datetime | None = None
    exercises: list[StrengthSessionExercise] = []
    last_working: StrengthSessionSet | None = None

    for record in fit.records:
        message = record.message
        if isinstance(message, SessionMessage) and start is None:
            start = _dt(message.start_time)
            continue
        if not isinstance(message, SetMessage):
            continue

        # Decoded fields come back as the raw enum value, not the enum member,
        # so compare on the value: 0 is REST, 1 is ACTIVE.
        set_type = message.set_type
        value = set_type.value if hasattr(set_type, "value") else set_type

        if value == SetType.REST.value:
            # A rest belongs to the set before it.
            if last_working is not None and message.duration:
                last_working.rest_s = float(message.duration)
            continue

        category_raw = message.category
        category_value = _first_value(category_raw)
        subtype_value = _first_value(message.category_subtype)
        if category_value is None:
            continue

        category = fx.category_name(int(category_value)) or "UNKNOWN"
        exercise_name = fx.exercise_name(int(category_value), subtype_value) or ""

        entry = StrengthSessionSet(
            reps=int(message.repetitions) if message.repetitions is not None else None,
            weight_kg=float(message.weight) if message.weight is not None else None,
            duration_s=float(message.duration) if message.duration is not None else None,
        )
        last_working = entry

        if (
            exercises
            and exercises[-1].category == category
            and exercises[-1].exercise_name == exercise_name
        ):
            exercises[-1].sets.append(entry)
        else:
            exercises.append(
                StrengthSessionExercise(
                    category=category, exercise_name=exercise_name, sets=[entry]
                )
            )

        if start is None:
            start = _dt(message.start_time)

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
