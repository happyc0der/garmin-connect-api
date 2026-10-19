"""Weight-scale FIT files: body composition into Garmin, and back out.

python-garminconnect can build one of these, but it converts timestamps with
``time.mktime``, which ignores the timezone and assumes the machine's own. On
a server whose clock is UTC that puts a morning weigh-in hours out. This uses
the same FIT library as the strength sessions, which works in absolute time.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fit_tool.exceptions import FitEncodingError
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_creator_message import FileCreatorMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.weight_scale_message import WeightScaleMessage
from fit_tool.profile.profile_type import FileType, Manufacturer
from pydantic import ValidationError

from ..errors import InvalidRequest
from ..models.health import BodyCompositionSpec

# Spec field -> FIT weight_scale field. Units already agree; fit-tool applies
# the FIT scale factors (x100 for kg and %, x4 for kcal, x10 for BMI) itself.
FIELDS = {
    "weight_kg": "weight",
    "percent_fat": "percent_fat",
    "percent_hydration": "percent_hydration",
    "visceral_fat_mass_kg": "visceral_fat_mass",
    "bone_mass_kg": "bone_mass",
    "muscle_mass_kg": "muscle_mass",
    "basal_met_kcal": "basal_met",
    "active_met_kcal": "active_met",
    "physique_rating": "physique_rating",
    "metabolic_age": "metabolic_age",
    "visceral_fat_rating": "visceral_fat_rating",
    "bmi": "bmi",
}


def _ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def measurement_serial(spec: BodyCompositionSpec) -> int:
    """A stable serial number from the measurement itself.

    The same weigh-in always encodes to the same file, so a sync that runs
    twice is caught by Garmin's duplicate check instead of logging it twice.
    Hashed from the file itself, so the same instant written in another
    timezone, or a value past FIT's precision, is the same weigh-in.
    """
    digest = hashlib.sha256(encode_body_composition(spec, serial=1)).digest()
    return (int.from_bytes(digest[:4], "big") & 0x7FFFFFFF) or 1


def encode_body_composition(spec: BodyCompositionSpec, *, serial: int | None = None) -> bytes:
    """A weigh-in as a FIT weight-scale file; a value FIT cannot hold is the request's fault."""
    try:
        return _encode(spec, serial)
    except FitEncodingError as exc:
        raise InvalidRequest(
            "This weigh-in cannot be written as a FIT file.", detail=str(exc)
        ) from exc


def _encode(spec: BodyCompositionSpec, serial: int | None) -> bytes:
    stamp = _ms(spec.timestamp)
    serial_number = serial if serial is not None else measurement_serial(spec)
    builder = FitFileBuilder(auto_define=True)

    file_id = FileIdMessage()
    file_id.type = FileType.WEIGHT
    file_id.manufacturer = Manufacturer.DEVELOPMENT.value
    file_id.product = 0
    file_id.serial_number = serial_number
    file_id.time_created = stamp
    builder.add(file_id)

    creator = FileCreatorMessage()
    creator.software_version = 100
    builder.add(creator)

    device = DeviceInfoMessage()
    device.timestamp = stamp
    device.manufacturer = Manufacturer.DEVELOPMENT.value
    device.serial_number = serial_number
    builder.add(device)

    scale = WeightScaleMessage()
    scale.timestamp = stamp
    for spec_field, fit_field in FIELDS.items():
        value = getattr(spec, spec_field)
        if value is not None:
            setattr(scale, fit_field, value)
    builder.add(scale)

    return bytes(builder.build().to_bytes())


def decode_body_composition(data: bytes) -> list[BodyCompositionSpec]:
    """Every weigh-in in a weight-scale FIT file."""
    from .decode import _unwrap, field_value, parse_fit

    fit = parse_fit(_unwrap(data))
    found: list[BodyCompositionSpec] = []
    for record in fit.records:
        message = record.message
        if not isinstance(message, WeightScaleMessage) or field_value(message, "weight") is None:
            continue
        values = {
            spec_field: field_value(message, fit_field)
            for spec_field, fit_field in FIELDS.items()
            if field_value(message, fit_field) is not None
        }
        values["timestamp"] = datetime.fromtimestamp(message.timestamp / 1000, tz=UTC)
        for key, value in values.items():
            if isinstance(value, float):
                values[key] = round(value, 2)
        try:
            found.append(BodyCompositionSpec.model_validate(values))
        except ValidationError as exc:
            raise InvalidRequest(
                "The FIT file holds a weigh-in out of range.", detail=str(exc.errors()[:3])
            ) from exc
    if not found:
        raise InvalidRequest("No weigh-ins found in this FIT file.")
    return found
