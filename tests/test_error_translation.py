"""How library failures reach a caller.

The question a caller most needs answered is whether Garmin said "no" or
never answered. These pin each case to the library's real exception types.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectInvalidFileFormatError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from garmin_api import auth
from garmin_api.errors import (
    GarminUnavailable,
    InvalidRequest,
    NotAuthenticated,
    NotFound,
    RateLimited,
)


def with_status(message: str, status: int | None) -> GarminConnectConnectionError:
    exc = GarminConnectConnectionError(message)
    if status is not None:
        exc.response = SimpleNamespace(status_code=status)
    return exc


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (GarminConnectTooManyRequestsError("429"), RateLimited),
        (GarminConnectAuthenticationError("401"), NotAuthenticated),
        (GarminConnectNotFoundError("API call client error (404): API Error 404"), NotFound),
        (GarminConnectInvalidFileFormatError("bad"), InvalidRequest),
        (with_status("x", 404), NotFound),
        (with_status("x", 400), InvalidRequest),
        (with_status("x", 403), InvalidRequest),
        (with_status("API call client error (409): conflict", None), InvalidRequest),
        (with_status("x", 503), GarminUnavailable),
        (with_status("Connection reset by peer", None), GarminUnavailable),
        (ValueError("month must be between 1 and 12, got: 13"), InvalidRequest),
    ],
)
def test_each_failure_maps_to_what_it_means(exc: Exception, expected: type) -> None:
    assert isinstance(auth.translate(exc), expected)


def test_only_a_network_failure_says_garmin_was_unreachable() -> None:
    unreachable = auth.translate(with_status("Connection reset by peer", None))
    server = auth.translate(with_status("x", 503))
    assert "Could not reach" in unreachable.message  # type: ignore[attr-defined]
    assert "server error" in server.message  # type: ignore[attr-defined]


def test_unrelated_errors_pass_through_untouched() -> None:
    """Our own bugs must still look like bugs, not like Garmin's answer."""
    original = KeyError("oops")
    assert auth.translate(original) is original
