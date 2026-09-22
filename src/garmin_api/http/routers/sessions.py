"""Logging completed sessions, and converting FIT files.

The write direction Garmin's own API has never offered: a finished strength
workout, with its sets, becoming an activity in the history.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Query, Response, UploadFile

from ...errors import InvalidRequest
from ...fit import decode_strength_session, encode_strength_session
from ...models.workouts import StrengthSessionSpec
from ...services import uploads as service

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/strength", operation_id="log_strength_session")
def log_strength(spec: StrengthSessionSpec) -> dict[str, Any]:
    """Log a completed strength workout to Garmin Connect.

    Takes the session as it happened, exercise by exercise with the reps and
    weight of every set, encodes it as a FIT activity file and uploads it. It
    appears in Garmin Connect as a Strength Training activity with the sets
    intact.

    Name exercises with Garmin's category and exercise_name pair; search the
    exercise catalogue first if unsure. Uploading the same session twice is
    reported as a duplicate rather than creating a second activity.
    """
    return service.log_strength_session(spec)


@router.post("/upload", operation_id="upload_activity_file")
async def upload(
    file: UploadFile = File(description="A .fit, .gpx or .tcx file."),
) -> dict[str, Any]:
    """Upload an activity file that already exists.

    Accepts the same formats as Garmin's own import: .fit, .gpx and .tcx.
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in service.ALLOWED_SUFFIXES:
        raise InvalidRequest(
            f"Cannot upload {suffix or 'a file with no extension'}.",
            detail="Garmin accepts .fit, .gpx and .tcx.",
        )
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / (file.filename or f"activity{suffix}")
        target.write_bytes(await file.read())
        return service.upload_file(target)


@router.post("/strength/encode", operation_id="encode_strength_session_to_fit")
def encode(spec: StrengthSessionSpec) -> Response:
    """Build the FIT file for a session without uploading it.

    For inspecting what would be sent, or for importing by hand through the
    Garmin Connect website.
    """
    data = encode_strength_session(spec)
    return Response(
        content=data,
        media_type="application/vnd.ant.fit",
        headers={"Content-Disposition": 'attachment; filename="session.fit"'},
    )


@router.post(
    "/strength/decode",
    operation_id="decode_fit_to_strength_session",
    response_model=StrengthSessionSpec,
)
async def decode(
    file: UploadFile = File(description="A .fit file, or the zip Garmin's export hands back."),
    name: str = Query("Strength Training"),
) -> StrengthSessionSpec:
    """Read a strength session out of a FIT file.

    The way to get a Garmin workout into another system: download an
    activity's original file, then decode it here into the same typed shape
    used to log one.
    """
    return decode_strength_session(await file.read(), name=name)
