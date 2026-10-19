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
from pathlib import Path
from typing import Any

from .. import auth, catalogue
from ..errors import InvalidRequest
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

    response = auth.call("upload_activity", str(source))
    return _interpret_upload(response)


def log_strength_session(
    spec: StrengthSessionSpec, *, rename: bool = True, serial: int | None = None
) -> dict[str, Any]:
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

    data = encode_strength_session(spec, serial=serial)

    with tempfile.TemporaryDirectory() as tmp:
        # The filename becomes the activity's name on Garmin's side, so it is
        # worth making it legible even though the rename usually supersedes it.
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.name).strip("_") or "strength"
        target = Path(tmp) / f"{safe}.fit"
        target.write_bytes(data)
        result = upload_file(target)

    activity_id = result.get("activity_id")
    if rename and activity_id:
        from . import activities

        try:
            activities.set_name(activity_id, spec.name)
            result["name"] = spec.name
        except Exception as exc:  # noqa: BLE001 -- the upload itself succeeded
            logger.warning("Uploaded %s but could not rename it: %s", activity_id, exc)
            result["rename_failed"] = str(exc)

    result["sets_uploaded"] = spec.total_sets
    return result


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

    return {"status": "unknown", "raw": payload}
