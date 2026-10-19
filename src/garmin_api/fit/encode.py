"""Building a FIT activity file for a completed strength session.

The message sequence Garmin expects, in order:

    file_id -> file_creator -> event(start) -> [record, set, ...] -> record
             -> lap -> session -> event(stop) -> activity

Records are timestamp-only samples at each set's start and at the end. The FIT
specification requires at least one in an activity file; Garmin accepted a
file without them, but Strava, receiving it from Garmin, then dated the session
at the moment it arrived rather than when it happened.

The sets are the point. Each carries the exercise (category plus optional
variant), the repetitions, the weight and how long it took. Garmin Connect
reads those back and shows a real strength activity, exercise by exercise.

Rests between sets are written as their own set messages with ``set_type``
REST, exactly as a watch records them, so the timeline has no holes.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_creator_message import FileCreatorMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile.profile_type import (
    Activity,
    Event,
    EventType,
    FileType,
    Manufacturer,
    SetType,
    Sport,
    SubSport,
)

from ..models.workouts import StrengthSessionSpec
from . import exercises as fx

# A rep takes a few seconds. Used only when the caller did not time the set,
# so that a session still has a plausible duration rather than zero.
SECONDS_PER_REP = 3.0
DEFAULT_REST_S = 60.0

# FIT counts its own seconds from 31 Dec 1989. Most fields in fit-tool take
# milliseconds since the Unix epoch and convert themselves, but local_timestamp
# is raw, so it has to be converted here.
FIT_EPOCH_OFFSET = 631065600


def _ms(moment: datetime) -> int:
    """FIT timestamps are milliseconds since the Unix epoch in fit-tool."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def _fit_local_seconds(moment: datetime) -> int:
    """The same instant as FIT's local_timestamp: FIT seconds plus the offset.

    This is how Garmin recovers the timezone a session happened in -- it
    compares this against the UTC timestamp. Get it wrong and a 7am workout
    shows up at 2am.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    offset = moment.utcoffset() or timedelta(0)
    return int(moment.timestamp() - FIT_EPOCH_OFFSET + offset.total_seconds())


def _record(moment: datetime) -> RecordMessage:
    """A timestamp-only sample, which places the activity in time."""
    record = RecordMessage()
    record.timestamp = _ms(moment)
    return record


def session_serial(spec: StrengthSessionSpec) -> int:
    """A stable serial number for a session, from its content.

    Same session in, same serial out. 31 bits because the field is an
    unsigned 32-bit integer in which zero means "invalid".
    """
    digest = hashlib.sha256(spec.model_dump_json().encode()).digest()
    return (int.from_bytes(digest[:4], "big") & 0x7FFFFFFF) or 1


def encode_strength_session(spec: StrengthSessionSpec, *, serial: int | None = None) -> bytes:
    """Encode a completed strength session as FIT bytes.

    ``serial`` is the file's serial number. By default it is derived from the
    session itself, so encoding the same session twice yields byte-identical
    files. That is what lets Garmin's duplicate check catch a sync that runs
    twice; a random serial would make every run look like a new activity.
    """
    builder = FitFileBuilder(auto_define=True)
    start = spec.start_time if spec.start_time.tzinfo else spec.start_time.replace(tzinfo=UTC)
    serial_number = serial if serial is not None else session_serial(spec)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.serial_number = serial_number
    file_id.time_created = _ms(start)
    builder.add(file_id)

    creator = FileCreatorMessage()
    creator.software_version = 100
    builder.add(creator)

    start_event = EventMessage()
    start_event.event = Event.TIMER
    start_event.event_type = EventType.START
    start_event.timestamp = _ms(start)
    builder.add(start_event)

    cursor = start
    total_active = 0.0
    message_index = 0

    for exercise in spec.exercises:
        # Validate before writing: a bad name must fail here, not silently
        # produce a file Garmin will reject or mislabel.
        category = fx.category_value(exercise.category)
        subtype = fx.subtype_value(exercise.category, exercise.exercise_name)

        for position, entry in enumerate(exercise.sets):
            reps = entry.reps or 0
            duration = entry.duration_s
            if duration is None:
                duration = max(reps * SECONDS_PER_REP, 1.0)

            builder.add(_record(cursor))
            working = SetMessage()
            working.message_index = message_index
            working.set_type = SetType.ACTIVE
            working.start_time = _ms(cursor)
            working.timestamp = _ms(cursor + timedelta(seconds=duration))
            working.duration = duration
            working.repetitions = reps
            working.category = [category]
            if subtype is not None:
                working.category_subtype = [subtype]
            if entry.weight_kg is not None:
                working.weight = float(entry.weight_kg)
            builder.add(working)

            message_index += 1
            cursor += timedelta(seconds=duration)
            total_active += duration

            # A rest after every set but the last of the last exercise; the
            # caller's own figure wins when they recorded one.
            is_final = exercise is spec.exercises[-1] and position == len(exercise.sets) - 1
            rest_s = (
                entry.rest_s if entry.rest_s is not None else (0.0 if is_final else DEFAULT_REST_S)
            )
            if rest_s > 0:
                builder.add(_record(cursor))
                rest = SetMessage()
                rest.message_index = message_index
                rest.set_type = SetType.REST
                rest.start_time = _ms(cursor)
                rest.timestamp = _ms(cursor + timedelta(seconds=rest_s))
                rest.duration = rest_s
                builder.add(rest)
                message_index += 1
                cursor += timedelta(seconds=rest_s)

    elapsed = (cursor - start).total_seconds()
    builder.add(_record(cursor))

    lap = LapMessage()
    lap.timestamp = _ms(cursor)
    lap.start_time = _ms(start)
    lap.total_elapsed_time = elapsed
    lap.total_timer_time = elapsed
    lap.sport = Sport.TRAINING
    lap.sub_sport = SubSport.STRENGTH_TRAINING
    lap.message_index = 0
    builder.add(lap)

    session = SessionMessage()
    session.timestamp = _ms(cursor)
    session.start_time = _ms(start)
    session.total_elapsed_time = elapsed
    session.total_timer_time = elapsed
    session.sport = Sport.TRAINING
    session.sub_sport = SubSport.STRENGTH_TRAINING
    session.first_lap_index = 0
    session.num_laps = 1
    session.message_index = 0
    builder.add(session)

    stop_event = EventMessage()
    stop_event.event = Event.TIMER
    stop_event.event_type = EventType.STOP_ALL
    stop_event.timestamp = _ms(cursor)
    builder.add(stop_event)

    activity = ActivityMessage()
    activity.timestamp = _ms(cursor)
    activity.total_timer_time = elapsed
    activity.num_sessions = 1
    activity.type = Activity.MANUAL
    activity.local_timestamp = _fit_local_seconds(cursor)
    builder.add(activity)

    return bytes(builder.build().to_bytes())
