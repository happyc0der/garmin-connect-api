"""Pre-release testing, 19 Oct 2026: bad inputs, and the login path."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from garmin_api import auth
from garmin_api.http.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path", ["/v1/activities/-5", "/v1/activities/0/sets", "/v1/workouts/-1", "/v1/workouts/0"]
)
def test_a_non_positive_id_is_refused_before_garmin(client: TestClient, path: str) -> None:
    """Garmin ids are positive; anything else is answered here, not by Garmin."""
    assert client.get(path).status_code == 422


def test_an_update_with_nothing_to_change_is_refused(client: TestClient) -> None:
    """It once answered success after making no call at all."""
    response = client.patch("/v1/activities/1")
    assert response.status_code == 400
    assert response.json()["message"] == "Nothing to update."


def test_week_summary_refuses_a_bad_date_plainly() -> None:
    """Rejected as a validation error naming the argument, not a parse crash."""
    from garmin_api.mcp import create_mcp

    with pytest.raises(Exception) as raised:
        asyncio.run(create_mcp().call_tool("week_summary", {"end_day": "garbage"}))
    message = str(raised.value)
    assert "end_day" in message
    assert "Invalid isoformat" not in message


class TestLogin:
    """The login path, against a stand-in for python-garminconnect's client."""

    @pytest.fixture
    def fake_garmin(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []

        class FakeClient:
            def dump(self, path: str) -> None:
                Path(path, "garmin_tokens.json").write_text('{"di_token": "t"}')

        class FakeGarmin:
            def __init__(self, email: str, password: str, prompt_mfa: Any) -> None:
                seen.append({"email": email, "password": password, "mfa": prompt_mfa})
                self.client = FakeClient()

            def login(self, tokenstore: str | None = None) -> None:
                seen[-1]["tokenstore"] = tokenstore
                seen[-1]["store_was_empty"] = not any(Path(tokenstore or "").iterdir())
                Path(tokenstore or "", "garmin_tokens.json").write_text("{}")

            def get_full_name(self) -> str:
                return "Test User"

        monkeypatch.setattr(auth, "Garmin", FakeGarmin)
        return seen

    def test_tokens_go_where_every_other_command_reads_them(
        self, fake_garmin: list[dict[str, Any]], no_real_tokens: Path
    ) -> None:
        name = auth.interactive_login("me@example.com", "secret", lambda: "123456")
        assert name == "Test User"
        assert auth.has_tokens()
        assert (no_real_tokens / auth.LOGIN_MARKER).is_file()

    def test_a_login_uses_the_password_even_with_tokens_stored(
        self, fake_garmin: list[dict[str, Any]], no_real_tokens: Path
    ) -> None:
        """Given the real store, the library would resume its tokens and ignore the password."""
        (no_real_tokens / "garmin_tokens.json").write_text('{"di_token": "account-a"}')
        auth.interactive_login("b@example.com", "secret", lambda: "123456")
        assert fake_garmin[0]["tokenstore"] != str(no_real_tokens)
        assert fake_garmin[0]["store_was_empty"]

    def test_the_password_is_not_kept_anywhere(
        self, fake_garmin: list[dict[str, Any]], no_real_tokens: Path
    ) -> None:
        auth.interactive_login("me@example.com", "s3cret-pass", lambda: "123456")
        for file in no_real_tokens.rglob("*"):
            assert "s3cret-pass" not in file.read_text()

    def test_the_cli_prompts_and_hides_the_password(
        self, fake_garmin: list[dict[str, Any]], no_real_tokens: Path
    ) -> None:
        from typer.testing import CliRunner

        from garmin_api.cli import app

        result = CliRunner().invoke(app, ["login"], input="me@example.com\ns3cret-pass\n")
        assert result.exit_code == 0, result.output
        assert "Logged in as Test User" in result.output
        assert "s3cret-pass" not in result.output, "a typed password must not be echoed"
