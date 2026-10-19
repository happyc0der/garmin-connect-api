"""Which device a workout is pushed to.

Garmin's "last used" device can be a pseudo-device named "Default" that is not
on the device list: uploading a file, a scale's weigh-in or a session logged by
this package puts it there. A workout pushed to it never reaches a watch.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from garmin_api.errors import InvalidRequest
from garmin_api.http.app import create_app
from garmin_api.services import workouts

WATCH = {
    "deviceId": 3500000001,
    "displayName": "Example Watch",
    "productDisplayName": "Example Watch",
    "primaryActivityTrackerIndicator": True,
    "deviceStatus": "active",
}
BIKE = {
    "deviceId": 3500000002,
    "displayName": "Example Bike Computer",
    "primaryActivityTrackerIndicator": False,
    "deviceStatus": "active",
}
PSEUDO_LAST_USED = {"userDeviceId": 2000000001, "lastUsedDeviceName": "Default"}


def pushed_to(fake_garmin: dict[str, Any]) -> list[Any]:
    return [args[1] for name, args in fake_garmin["calls"] if name == "push_workout_to_device"]


@pytest.fixture
def account(fake_garmin: dict[str, Any]) -> dict[str, Any]:
    fake_garmin["responses"].update(
        {
            "get_devices": [WATCH, BIKE],
            "get_device_last_used": PSEUDO_LAST_USED,
            "push_workout_to_device": None,
        }
    )
    return fake_garmin


def test_a_pseudo_last_used_device_is_passed_over_for_the_primary_tracker(account) -> None:
    target = workouts.push_to_device(7)
    assert target.device_id == WATCH["deviceId"]
    assert pushed_to(account) == [WATCH["deviceId"]]


def test_a_real_last_used_device_is_used(account) -> None:
    account["responses"]["get_device_last_used"] = {"userDeviceId": BIKE["deviceId"]}
    assert workouts.push_to_device(7).device_id == BIKE["deviceId"]


def test_the_only_device_is_used_when_none_is_primary(account) -> None:
    account["responses"]["get_devices"] = [BIKE]
    assert workouts.push_to_device(7).device_id == BIKE["deviceId"]


def test_an_ambiguous_choice_asks_and_lists_the_devices(account) -> None:
    account["responses"]["get_devices"] = [
        {**WATCH, "primaryActivityTrackerIndicator": False},
        BIKE,
    ]
    with pytest.raises(InvalidRequest) as caught:
        workouts.push_to_device(7)
    assert "Example Watch (3500000001)" in (caught.value.detail or "")
    assert pushed_to(account) == []


def test_a_device_not_on_the_account_is_refused(account) -> None:
    with pytest.raises(InvalidRequest, match="not on this account"):
        workouts.push_to_device(7, PSEUDO_LAST_USED["userDeviceId"])
    assert pushed_to(account) == []


def test_an_account_with_no_devices_says_so(account) -> None:
    account["responses"]["get_devices"] = []
    with pytest.raises(InvalidRequest, match="No devices"):
        workouts.push_to_device(7)


def test_the_route_names_the_device_it_pushed_to(account) -> None:
    response = TestClient(create_app()).post("/v1/workouts/7/push")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "workout_id": 7,
        "device_id": WATCH["deviceId"],
        "device_name": "Example Watch",
        "status": "queued for device",
    }
