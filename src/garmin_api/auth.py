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

from .errors import GarminUnavailable, NotAuthenticated, RateLimited

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


def has_tokens() -> bool:
    d = token_dir()
    return d.is_dir() and any(d.glob("oauth*token*.json"))


def token_age_days() -> float | None:
    """Age of the oldest stored token, or None if there are none.

    The OAuth1 token is good for about a year and the OAuth2 one for hours, so
    this is a rough health signal rather than an expiry.
    """
    files = sorted(token_dir().glob("oauth*token*.json"))
    if not files:
        return None
    oldest = min(f.stat().st_mtime for f in files)
    return (datetime.now(UTC).timestamp() - oldest) / 86400


def translate(exc: Exception) -> Exception:
    """Map a library or transport exception onto this package's vocabulary."""
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
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
    if isinstance(exc, GarminConnectConnectionError):
        return GarminUnavailable("Could not reach Garmin Connect.", detail=str(exc))
    return exc


class Session:
    """Holds the one authenticated client and hands it out under a lock."""

    def __init__(self, tokens: Path | None = None) -> None:
        self._tokens = tokens or token_dir()
        self._client: Garmin | None = None
        self._lock = threading.RLock()

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

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run one Garmin call, translating failures.

        A 401 mid-session means the cached client went stale, so the client is
        dropped and rebuilt from the stored tokens exactly once. A second
        failure is reported rather than retried.
        """
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if _is_unauthorized(exc):
                with self._lock:
                    self._client = None
                logger.info("Garmin returned 401; reloading tokens and retrying once")
                self.client()
                try:
                    return fn(*args, **kwargs)
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
    s = session()
    bound = getattr(s.client(), fn_name)
    return s.call(bound, *args, **kwargs)


def interactive_login(
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    tokens: Path | None = None,
) -> str:
    """Log in with a password and store the tokens. Only the CLI calls this.

    Returns the display name so the caller can confirm whose account it is.
    """
    target = tokens or token_dir()
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
