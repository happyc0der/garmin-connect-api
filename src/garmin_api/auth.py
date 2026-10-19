"""The authenticated Garmin client, and the token store behind it.

The rules this module exists to enforce:

* A password is typed in exactly one place, ``garmin login``, at a terminal.
  The HTTP service and the MCP server never see one and never prompt; without
  a usable token they raise :class:`NotAuthenticated` and say to run the CLI.
* Tokens are reused. Garmin put Cloudflare fingerprinting and per-account rate
  buckets in front of the SSO endpoints in March 2026, and a run of failed
  logins locks the account out for a day or more. Logging in is rare and
  deliberate; refreshing is the library's job.
* One client, one lock. Concurrent HTTP or MCP calls share a single
  ``Garmin`` instance rather than racing to authenticate separately.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from .errors import (
    Duplicate,
    GarminUnavailable,
    InvalidRequest,
    NotAuthenticated,
    NotFound,
    RateLimited,
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = Path.home() / ".garmin-api" / "default"


def token_dir() -> Path:
    """Where tokens live.

    ``GARMINTOKENS`` is honoured because python-garminconnect and the various
    community tools all read it; point it at the same directory and one login
    serves all of them.
    """
    env = os.getenv("GARMINTOKENS")
    return Path(env).expanduser() if env else DEFAULT_TOKEN_DIR


def _token_files() -> list[Path]:
    """This package's token file, if present.

    python-garminconnect 0.3 keeps everything in one ``garmin_tokens.json``,
    and its own ``token_file_path`` is asked where that is so the two cannot
    disagree. The older garth files (``oauth1_token.json``, ``oauth2_token.json``)
    are deliberately not counted: 0.3.19 refuses them, so reporting them as a
    login would say "authenticated" while every call fails.
    """
    from garminconnect.client import token_file_path

    try:
        current = token_file_path(str(token_dir()))
    except ValueError:
        return []
    return [current] if current.is_file() else []


def legacy_tokens() -> bool:
    """Whether only old garth-format tokens are present, which need a fresh login."""
    directory = token_dir()
    return directory.is_dir() and any(directory.glob("oauth*_token.json")) and not _token_files()


def has_tokens() -> bool:
    return bool(_token_files())


def token_age_days() -> float | None:
    """Days since the tokens were last written, or None if there are none.

    Garmin issues a short-lived access token with a longer refresh token, and
    the library rewrites the file when it refreshes, so a large number here
    means the tokens have not been used in a long while rather than that they
    have expired.
    """
    files = _token_files()
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return (datetime.now(UTC).timestamp() - newest) / 86400


def _status_of(exc: BaseException) -> int | None:
    """The HTTP status behind a library error, if it carries one.

    python-garminconnect keeps the original response on ``.response`` and also
    writes the status into the message, as in "client error (404)".
    """
    import re

    # Walk the cause chain: the library re-raises upload failures as
    # "Import error: ..." from the original, which is where the status lives.
    seen: BaseException | None = exc
    for _ in range(5):
        if seen is None:
            break
        response = getattr(seen, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
        match = re.search(r"\((\d{3})\)|API Error (\d{3})", str(seen))
        if match:
            return int(match.group(1) or match.group(2))
        seen = seen.__cause__ or seen.__context__
    return None


def translate(exc: Exception) -> Exception:
    """Map a library or transport exception onto this package's vocabulary.

    The distinction that matters most to a caller is between Garmin answering
    "no" and Garmin not answering at all. A missing activity is a 404 and a
    malformed request a 400; only a network failure means Garmin could not be
    reached, and only a 5xx means Garmin itself is having trouble.
    """
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectInvalidFileFormatError,
        GarminConnectNotFoundError,
        GarminConnectTooManyRequestsError,
    )

    if isinstance(exc, GarminConnectTooManyRequestsError):
        return RateLimited(
            "Garmin is rate limiting this account.",
            detail=(
                "Wait before trying again -- repeated failures against the login "
                "endpoints can lock an account out for 24-72 hours. Do not retry in a loop."
            ),
        )
    if isinstance(exc, GarminConnectAuthenticationError):
        return NotAuthenticated(
            "Garmin rejected the stored credentials.",
            detail="Run `garmin login` to authenticate again.",
        )
    if isinstance(exc, GarminConnectNotFoundError):
        return NotFound("Garmin has no such item.", detail=str(exc))
    if isinstance(exc, GarminConnectInvalidFileFormatError):
        return InvalidRequest("Garmin does not accept this file format.", detail=str(exc))
    if isinstance(exc, GarminConnectConnectionError):
        if "already exists (duplicate)" in str(exc):
            return Duplicate("Garmin already has this activity.", detail=str(exc))
        status = _status_of(exc)
        # Writes go through the library's transport directly rather than its
        # error mapper, so a 429 or 401 on a write arrives here as a plain
        # connection error carrying the status. They must still mean "stop".
        if status == 429:
            return translate(GarminConnectTooManyRequestsError(str(exc)))
        if status == 401:
            return translate(GarminConnectAuthenticationError(str(exc)))
        if status == 404:
            return NotFound("Garmin has no such item.", detail=str(exc))
        if status is not None and 400 <= status < 500:
            return InvalidRequest(f"Garmin rejected the request ({status}).", detail=str(exc))
        if status is not None and status >= 500:
            return GarminUnavailable(
                f"Garmin answered with a server error ({status}).",
                detail="The library already retried. Try again later.",
            )
        return GarminUnavailable("Could not reach Garmin Connect.", detail=str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        # Raised by the library's own input checks before any request is made.
        return InvalidRequest("Garmin's client refused the input.", detail=str(exc))
    return exc


class Session:
    """Holds the one authenticated client and hands it out under a lock."""

    def __init__(self, tokens: Path | None = None) -> None:
        # Resolved on use rather than captured here, so the location has one
        # source of truth: the same call has_tokens() makes.
        self._tokens_override = tokens
        self._client: Garmin | None = None
        self._lock = threading.RLock()

    @property
    def _tokens(self) -> Path:
        return self._tokens_override or token_dir()

    @property
    def token_path(self) -> Path:
        return self._tokens

    def client(self) -> Garmin:
        """Return the authenticated client, resuming from stored tokens.

        Never prompts and never uses a password: a missing or rejected token is
        an error the caller has to resolve with ``garmin login``.
        """
        with self._lock:
            if self._client is not None:
                return self._client
            if not has_tokens():
                raise NotAuthenticated(
                    "No Garmin tokens found.",
                    detail=f"Run `garmin login` first. Tokens are stored in {self._tokens}.",
                )
            client = Garmin()
            try:
                client.login(tokenstore=str(self._tokens))
            except Exception as exc:  # noqa: BLE001 -- re-raised as our own type
                raise translate(exc) from exc
            self._client = client
            return client

    def call(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run one Garmin call by method name, translating failures.

        A 401 mid-session means the cached client went stale, so it is rebuilt
        from the stored tokens and the call is made once more -- on the new
        client, which is why the method is looked up by name each time. A
        second failure is reported rather than retried.
        """
        try:
            return getattr(self.client(), fn_name)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if _is_unauthorized(exc):
                with self._lock:
                    self._client = None
                logger.info("Garmin returned 401; reloading tokens and retrying once")
                try:
                    return getattr(self.client(), fn_name)(*args, **kwargs)
                except Exception as retry_exc:  # noqa: BLE001
                    raise translate(retry_exc) from retry_exc
            raise translate(exc) from exc

    def reset(self) -> None:
        with self._lock:
            self._client = None


def _is_unauthorized(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 401:
        return True
    return "401" in str(exc) and "unauthor" in str(exc).lower()


_session: Session | None = None
_session_lock = threading.Lock()


def session() -> Session:
    """The process-wide session."""
    global _session
    with _session_lock:
        if _session is None:
            _session = Session()
        return _session


def client() -> Garmin:
    return session().client()


def call(fn_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a python-garminconnect method by name, with error translation.

    Services use this rather than touching the client directly, so every
    Garmin call goes through the same locking and error mapping.
    """
    return session().call(fn_name, *args, **kwargs)


def interactive_login(email: str, password: str, prompt_mfa: Callable[[], str]) -> str:
    """Log in with a password and store the tokens. Only the CLI calls this.

    Returns the display name so the caller can confirm whose account it is.
    """
    target = token_dir()
    target.mkdir(parents=True, exist_ok=True)
    garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    try:
        # Passing the token store makes the library persist the tokens itself
        # once the login succeeds, including through the MFA round trip.
        garmin.login(tokenstore=str(target))
        name = garmin.get_full_name() or email
    except Exception as exc:  # noqa: BLE001
        raise translate(exc) from exc
    session().reset()
    return str(name)
