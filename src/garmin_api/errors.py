"""The error vocabulary shared by the library, the HTTP service and the MCP.

Garmin's own failures are noisy and inconsistent -- an HTML challenge page, a
bare 403, a 429 with no body. These translate them into a handful of errors a
caller can actually branch on, each carrying the HTTP status the service should
answer with.
"""

from __future__ import annotations


class GarminApiError(Exception):
    """Base class. ``status`` is what the HTTP layer returns for it."""

    status = 500

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, str | None]:
        return {"error": type(self).__name__, "message": self.message, "detail": self.detail}


class NotAuthenticated(GarminApiError):
    """No usable token on disk, or the stored one was rejected.

    Deliberately never triggers a password login: the service has no password
    and must not prompt for one. The caller runs ``garmin login`` instead.
    """

    status = 401


class RateLimited(GarminApiError):
    """Garmin returned 429.

    Worth taking seriously. Since Garmin put Cloudflare in front of the login
    endpoints in March 2026, repeated failures lock an account out for 24-72
    hours, so nothing here retries automatically.
    """

    status = 429


class NotFound(GarminApiError):
    status = 404


class InvalidRequest(GarminApiError):
    """The request was wrong before it ever reached Garmin.

    Chiefly an exercise category or name that is not in Garmin's catalogue,
    which Garmin itself rejects with an unhelpful 400.
    """

    status = 400


class GarminUnavailable(GarminApiError):
    """Garmin answered, but not with anything usable."""

    status = 502
