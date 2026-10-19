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
    GarminApiError,
    GarminUnavailable,
    InvalidRequest,
    NotAuthenticated,
    NotFound,
    RateLimited,
)

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = Path.home() / ".garmin-api" / "default"

# Written by `garmin login` with a fresh id each time, and removed by `garmin
# logout`. The library rewrites the token file whenever it refreshes, so the
# file itself cannot tell a running server that someone logged in again,
# perhaps as someone else; this can.
LOGIN_MARKER = ".garmin-api-login"


def _login_marker(directory: Path) -> str | None:
    try:
        return (directory / LOGIN_MARKER).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def token_dir() -> Path:
    """Where tokens live.

    ``GARMINTOKENS`` is honoured because python-garminconnect and the various
    community tools all read it; point it at the same directory and one login
    serves all of them.
    """
    env = os.getenv("GARMINTOKENS")
    return Path(env).expanduser() if env else DEFAULT_TOKEN_DIR


def _token_files(directory: Path | None = None) -> list[Path]:
    """This package's token file, if present.

    python-garminconnect 0.3 keeps everything in one ``garmin_tokens.json``,
    and its own ``token_file_path`` is asked where that is so the two cannot
    disagree. The older garth files (``oauth1_token.json``, ``oauth2_token.json``)
    are deliberately not counted: 0.3.19 refuses them, so reporting them as a
    login would say "authenticated" while every call fails.
    """
    from garminconnect.client import token_file_path

    try:
        current = token_file_path(str(directory or token_dir()))
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
    try:
        newest = max(f.stat().st_mtime for f in files)
    except FileNotFoundError:  # removed by a logout since it was found
        return None
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

    if isinstance(exc, GarminConnectTooManyRequestsError) or _status_of(exc) == 429:
        # A 429 can also arrive wrapped as an authentication error, when it
        # meets the profile fetch that loading tokens makes. Told to log in
        # again, a user would walk into the SSO lockout this exists to avoid.
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
        if status == 409:
            # The upload service's answer to a file it already has; the
            # library's import path says so in words, its upload path only
            # with the status.
            return Duplicate("Garmin already has this.", detail=str(exc))
        if status is not None and 400 <= status < 500:
            return InvalidRequest(f"Garmin rejected the request ({status}).", detail=str(exc))
        if status is not None and status >= 500:
            return GarminUnavailable(
                f"Garmin answered with a server error ({status}).",
                detail="The library already retried. Try again later.",
            )
        return GarminUnavailable("Could not reach Garmin Connect.", detail=str(exc))
    import requests

    if isinstance(exc, requests.Timeout):
        # Writes reach the transport without the library's error mapper, so
        # the network's own errors arrive here unwrapped.
        return GarminUnavailable(
            "Garmin did not answer in time.",
            detail="A write may or may not have been applied; check before repeating it.",
        )
    if isinstance(exc, requests.RequestException):
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
        self._client_login: str | None = None
        # The stored login Garmin last refused, so calls fail at once rather
        # than each trying it again, until a new login replaces it.
        self._refused: tuple[str | None, int] | None = None
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
            present = bool(_token_files(self._tokens))
            if self._client is not None:
                if present and _login_marker(self._tokens) == self._client_login:
                    return self._client
                # Logged out, or logged in again, perhaps as someone else.
                self._discard()
            if not present:
                raise NotAuthenticated(
                    "No Garmin tokens found.",
                    detail=f"Run `garmin login` first. Tokens are stored in {self._tokens}.",
                )
            if self._refused is not None and self._refused == self._stored():
                raise NotAuthenticated(
                    "Garmin rejected the stored credentials.",
                    detail="Run `garmin login` to authenticate again.",
                )
            client = Garmin()
            try:
                client.login(tokenstore=str(self._tokens))
            except Exception as exc:  # noqa: BLE001 -- re-raised as our own type
                error = translate(exc)
                if isinstance(error, NotAuthenticated):
                    self._refused = self._stored()
                raise error from exc
            # The library loads the profile's timezone on first use, without a
            # lock, and a caller racing that load gets the machine's timezone.
            # Loaded here, under this lock, before anyone else can use it.
            if client.get_user_timezone() is None and hasattr(client, "_time_zone_loaded"):
                client._time_zone_loaded = False  # try again on next use
            self._client = client
            self._client_login = _login_marker(self._tokens)
            self._refused = None
            return client

    def _stored(self) -> tuple[str | None, int]:
        """What is on disk now: the login marker and the token file's mtime."""
        files = _token_files(self._tokens)
        try:
            mtime = files[0].stat().st_mtime_ns if files else 0
        except FileNotFoundError:
            mtime = 0
        return _login_marker(self._tokens), mtime

    def _discard(self) -> None:
        """Drop the cached client, and stop it writing its tokens back.

        A call still running on it would otherwise save the old account's
        refreshed tokens over a new login's.
        """
        inner = getattr(self._client, "client", None)
        if inner is not None and hasattr(inner, "_tokenstore_path"):
            inner._tokenstore_path = None
        self._client = None

    def call(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        """Run one Garmin call by method name, translating failures.

        A 401 mid-session means the cached client went stale, so it is rebuilt
        from the stored tokens and the call is made once more -- on the new
        client, which is why the method is looked up by name each time. A
        second failure is reported rather than retried.
        """
        client = self.client()
        try:
            return getattr(client, fn_name)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if not _is_unauthorized(exc):
                raise translate(exc) from exc
            with self._lock:
                # Only the client that failed is dropped: several calls failing
                # at once must not each throw away the one another just built.
                if self._client is client:
                    self._discard()
            logger.info("Garmin returned 401; reloading tokens and retrying once")
            retry = self.client()
            try:
                return getattr(retry, fn_name)(*args, **kwargs)
            except Exception as retry_exc:  # noqa: BLE001
                if _is_unauthorized(retry_exc):
                    with self._lock:
                        if self._client is retry:
                            self._discard()
                        self._refused = self._stored()
                raise translate(retry_exc) from retry_exc

    def reset(self) -> None:
        with self._lock:
            self._discard()
            self._refused = None


def _is_unauthorized(exc: Exception) -> bool:
    """Whether Garmin refused the client's token, as opposed to anything else.

    A read's 401 comes back from the library's error mapper as an
    authentication error carrying the status only in its cause, and a
    write's as a connection error with the response attached; both count.
    This package's own errors, such as missing tokens, never do.
    """
    from garminconnect import GarminConnectAuthenticationError

    if isinstance(exc, GarminApiError):
        return False
    return isinstance(exc, GarminConnectAuthenticationError) or _status_of(exc) == 401


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


def in_account_time(moment: datetime) -> datetime:
    """``moment``, with the Garmin account's own timezone if it had no offset.

    A model or a person saying "this morning at 7" means 7 where the account
    is, and Garmin files the activity or weigh-in by that local time.
    """
    if moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=call("_user_tzinfo"))


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
    import tempfile
    import uuid

    target = token_dir()
    target.mkdir(parents=True, exist_ok=True)
    garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    with tempfile.TemporaryDirectory() as fresh:
        try:
            # An empty store, so the password is used: given the real one, the
            # library would resume the stored tokens and ignore it, keeping
            # whichever account was logged in before.
            garmin.login(tokenstore=fresh)
            name = garmin.get_full_name() or email
        except Exception as exc:  # noqa: BLE001
            raise translate(exc) from exc
        garmin.client.dump(str(target))
    marker = target / LOGIN_MARKER
    marker.write_text(uuid.uuid4().hex, encoding="utf-8")
    marker.chmod(0o600)
    session().reset()
    return str(name)
