"""Token discovery, against both layouts a token directory can hold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from garmin_api import auth


def test_no_files_means_no_tokens(no_real_tokens: Path) -> None:
    assert auth.has_tokens() is False
    assert auth.token_age_days() is None


def test_current_library_layout_is_recognised(no_real_tokens: Path) -> None:
    """python-garminconnect 0.3 writes a single garmin_tokens.json."""
    (no_real_tokens / "garmin_tokens.json").write_text(json.dumps({"di_token": "x"}))
    assert auth.has_tokens() is True
    assert auth.token_age_days() is not None


def test_older_garth_layout_is_recognised(no_real_tokens: Path) -> None:
    (no_real_tokens / "oauth1_token.json").write_text("{}")
    (no_real_tokens / "oauth2_token.json").write_text("{}")
    assert auth.has_tokens() is True


def test_session_follows_the_token_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session must look where has_tokens() looks, even if that moves."""
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("GARMINTOKENS", str(first))
    session = auth.Session()
    assert session.token_path == first

    monkeypatch.setenv("GARMINTOKENS", str(second))
    assert session.token_path == second
