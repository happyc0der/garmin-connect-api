"""Getting completed activities into Garmin Connect.

The write Garmin's official developer programme has never offered. Their
Training API publishes plans; nothing in it uploads a finished workout. The
upload service the Connect website uses does accept one, and a FIT file with
set messages arrives as a proper Strength Training activity, exercise by
exercise.

Two ways in: a file that already exists (:func:`upload_file`), or a session
described in JSON that is encoded here first (:func:`log_strength_session`).
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from .. import auth, catalogue
from ..errors import Duplicate, InvalidRequest, NotAuthenticated, RateLimited
from ..fit import encode_strength_session
from ..models.workouts import StrengthSessionSpec

logger = logging.getLogger(__name__)

ALLOWED_SUFFIXES = {".fit", ".gpx", ".tcx"}


def upload_file(path: str | Path) -> dict[str, Any]:
    """Upload an activity file. Accepts .fit, .gpx and .tcx.

    Garmin deduplicates on the file's own identity, so re-uploading one it has
    already seen is reported as a duplicate rather than creating a second
    activity. That is the desired behaviour for a sync that runs twice, so it
    is surfaced plainly instead of being treated as a failure.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise InvalidRequest(f"No file at {source}.")
    if source.suffix.lower() not in ALLOWED_SUFFIXES:
        raise InvalidRequest(
            f"Cannot upload {source.suffix or 'a file with no extension'}.",
            detail="Garmin accepts .fit, .gpx and .tcx.",
        )

    try:
        response = auth.call("upload_activity", str(source))
    except Duplicate as exc:
        # The library raises on Garmin's 409 rather than returning it. The
        # reply it raised from still names the activity already there.
        return {
            "status": "duplicate",
            "activity_id": _existing_activity_id(exc),
            "messages": [exc.detail or exc.message],
        }
    return _interpret_upload(response)


def _existing_activity_id(exc: BaseException) -> int | None:
    """The id in a 409 reply's ``detailedImportResult``, found through the cause chain."""
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            return None
        response = getattr(seen, "response", None)
        if response is not None and hasattr(response, "json"):
            try:
                failures = (response.json().get("detailedImportResult") or {}).get("failures")
                found = (failures or [{}])[0].get("internalId")
            except Exception:  # noqa: BLE001 -- a body that is not the expected JSON
                found = None
            if isinstance(found, int):
                return found
        seen = seen.__cause__ or seen.__context__
    return None


# One session logged at a time in a process: between the look before an upload
# and the look after it, another log's activity would look like this one's.
_logging = threading.Lock()


def log_strength_session(
    spec: StrengthSessionSpec, *, rename: bool = True, serial: int | None = None
) -> dict[str, Any]:
    """Encode a completed strength session and upload it; see :func:`_log`."""
    with _logging:
        return _log(spec, rename=rename, serial=serial)


def _log(spec: StrengthSessionSpec, *, rename: bool, serial: int | None) -> dict[str, Any]:
    """Encode a completed strength session and upload it.

    Every exercise is validated against Garmin's catalogue first, so a bad
    name fails here with a useful message rather than producing a file Garmin
    silently mislabels.

    Garmin names an uploaded activity after the file, not after anything in
    it, so the session's name is applied afterwards with a rename. Set
    ``rename=False`` to skip that second call.
    """
    for exercise in spec.exercises:
        catalogue.validate(exercise.category, exercise.exercise_name)

    if spec.start_time.tzinfo is None:
        spec = spec.model_copy(update={"start_time": auth.in_account_time(spec.start_time)})
    data = encode_strength_session(spec, serial=serial)
    # Anything already starting at this instant, such as the watch's own
    # recording or an earlier log of the session, is not the new upload. If
    # that cannot be read, the upload still goes ahead, but an "accepted"
    # reply is then left as it is rather than guessed at.
    already_there: set[int] | None
    try:
        already_there = set(_ids_starting_at(spec))
    except (NotAuthenticated, RateLimited):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list activities before uploading: %s", exc)
        already_there = None

    with tempfile.TemporaryDirectory() as tmp:
        # The filename becomes the activity's name on Garmin's side, so it is
        # worth making it legible even though the rename usually supersedes it.
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.name).strip("_") or "strength"
        target = Path(tmp) / f"{safe}.fit"
        target.write_bytes(data)
        result = upload_file(target)

    activity_id = result.get("activity_id")
    found_later = already_there is not None
    if activity_id is None and found_later and result.get("status") in ("accepted", "processed"):
        # Garmin often accepts an activity file and processes it afterwards,
        # answering with neither a success nor a failure. Found live: the
        # activity appeared moments later. The session's own start time is
        # known exactly, so the new activity is found by it.
        try:
            candidates = _new_ids_at(spec, exclude=already_there)
        except Exception as exc:  # noqa: BLE001 -- the upload itself succeeded
            logger.warning("Uploaded, but could not look the activity up: %s", exc)
            result["find_failed"] = str(exc)
            candidates = []
        if len(candidates) == 1:
            activity_id = candidates[0]
            result["status"] = "created"
            result["activity_id"] = activity_id
        elif candidates:
            # Several new activities start at this second (a watch syncing
            # its own recording, say): which is the upload cannot be told.
            result["candidates"] = candidates
    # Only a new activity is named and described. A duplicate's id is the
    # activity already there, which this call must not change.
    if activity_id and result.get("status") == "created":
        _label(result, activity_id, spec, rename=rename)
        others = sorted((already_there or set()) - {activity_id})
        if others:
            result["also_at_this_time"] = others
            if "note" in result:
                # Rate limited already: say what is there, but ask for no
                # further call against a throttled account.
                result["note"] += f" Other activities also start at this second: {others}."
            else:
                result["note"] = (
                    f"Other activities already start at this second: {others}. If one is this "
                    "same session, delete the extra with delete_activity."
                )
    elif result.get("status") in ("accepted", "processed"):
        result["note"] = (
            "Garmin is still processing the upload, so its name and notes are not applied "
            "yet. Find it later with list_activities by its start time, then name it with "
            "update_activity."
        )

    result["sets_uploaded"] = spec.total_sets
    result["duration_s"] = _duration(data)
    return result


def _duration(data: bytes) -> float | None:
    """The session's length as its file records it: sets, defaults and rests."""
    from fit_tool.fit_file import FitFile
    from fit_tool.profile.messages.session_message import SessionMessage

    for record in FitFile.from_bytes(data).records:
        if isinstance(record.message, SessionMessage):
            return record.message.total_elapsed_time
    return None


def _label(
    result: dict[str, Any], activity_id: int, spec: StrengthSessionSpec, *, rename: bool
) -> None:
    """Give a new activity the session's name and notes, which the file cannot carry."""
    from . import activities

    steps = [("name", spec.name if rename else None), ("description", spec.notes)]
    for field, value in steps:
        if not value:
            continue
        try:
            if field == "name":
                activities.set_name(activity_id, value)
            else:
                activities.set_description(activity_id, value)
            result[field] = value
        except (RateLimited, NotAuthenticated) as exc:
            # The upload stands, but no further call should follow this.
            result["rename_failed" if field == "name" else "description_failed"] = str(exc)
            result["note"] = f"Logged, but not named or described: {exc.message}"
            return
        except Exception as exc:  # noqa: BLE001 -- the upload itself succeeded
            logger.warning("Uploaded %s but could not set its %s: %s", activity_id, field, exc)
            result["rename_failed" if field == "name" else "description_failed"] = str(exc)


# How long to wait for Garmin to finish processing an accepted upload.
FIND_ATTEMPTS = 6
FIND_INTERVAL_S = 2.0


def find_uploaded_session(
    spec: StrengthSessionSpec,
    *,
    attempts: int | None = None,
    interval_s: float | None = None,
    exclude: set[int] | None = None,
) -> int | None:
    """The id of the strength activity that starts when ``spec`` does, if any.

    Matches on the start instant to the second, which the encoder writes
    exactly, so an unrelated workout the same day is never mistaken for it.
    Ids in ``exclude``, the ones there before an upload, are passed over.
    """
    found = _new_ids_at(spec, exclude=exclude, attempts=attempts, interval_s=interval_s)
    # Several at once cannot be told apart; better none than the wrong one.
    return found[0] if len(found) == 1 else None


def _new_ids_at(
    spec: StrengthSessionSpec,
    *,
    exclude: set[int] | None = None,
    attempts: int | None = None,
    interval_s: float | None = None,
) -> list[int]:
    """New strength activities starting when ``spec`` does, waiting for the first to show."""
    import time

    # Read at call time, not bound as defaults, so the settings can be changed.
    attempts = FIND_ATTEMPTS if attempts is None else attempts
    interval_s = FIND_INTERVAL_S if interval_s is None else interval_s
    for attempt in range(attempts):
        found = [i for i in _ids_starting_at(spec) if i not in (exclude or set())]
        if found:
            return found
        if attempt < attempts - 1:
            time.sleep(interval_s)
    return []


def _ids_starting_at(spec: StrengthSessionSpec) -> list[int]:
    """Strength activities starting within a second of ``spec``."""
    from datetime import timedelta

    from . import activities

    start = spec.start_time
    rows = activities.list_activities(
        activity_type=activities.STRENGTH,
        start_date=(start - timedelta(days=1)).date(),
        end_date=(start + timedelta(days=1)).date(),
        limit=50,
    )
    return [
        row.activity_id
        for row in rows
        if row.start_time and abs((row.start_time - start).total_seconds()) <= 1
    ]


def _interpret_upload(response: Any) -> dict[str, Any]:
    """Pull the activity id out of Garmin's upload response.

    The shape varies: a successful upload nests the new id under
    ``detailedImportResult.successes``, while a duplicate reports it under
    ``failures`` with a message saying so. Both are reported honestly.
    """
    payload: dict[str, Any] = {}
    if hasattr(response, "json"):
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {}
    elif isinstance(response, dict):
        payload = response

    result = payload.get("detailedImportResult") or {}
    successes = result.get("successes") or []
    failures = result.get("failures") or []

    if successes:
        first = successes[0]
        activity_id = first.get("internalId") or first.get("id")
        if activity_id is None:
            # Weight and other non-activity files: Garmin answers "File
            # processed." with no id, and never signals a duplicate. Verified
            # live: an identical weigh-in uploaded three times is kept once,
            # so "processed" is all that can honestly be said.
            return {"status": "processed", "activity_id": None, "raw": payload}
        return {"status": "created", "activity_id": activity_id, "raw": payload}

    if failures:
        messages = [
            message.get("content") or message.get("message") or ""
            for failure in failures
            for message in (failure.get("messages") or [])
        ]
        joined = " ".join(messages).lower()
        status = "duplicate" if "duplicate" in joined else "rejected"
        return {
            "status": status,
            "activity_id": (failures[0].get("internalId") if status == "duplicate" else None),
            "messages": messages,
            "raw": payload,
        }

    if result.get("uploadId") or result.get("uploadUuid") or result == {}:
        # Accepted for processing: no success or failure yet, and no id.
        return {
            "status": "accepted",
            "activity_id": None,
            "upload_id": result.get("uploadId"),
            "raw": payload,
        }
    return {"status": "unknown", "raw": payload}
