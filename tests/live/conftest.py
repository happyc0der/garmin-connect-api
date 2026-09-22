"""Live tests run against the real Garmin account, and are skipped by default.

Enable with GARMIN_LIVE=1, and only with tokens already in place: these call
Garmin for real, and a burst of failed calls can get an account rate limited
for a day. They are deliberately few and gentle.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from garmin_api import auth


@pytest.fixture(autouse=True)
def real_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the offline suite's token isolation; these tests need the real ones."""
    monkeypatch.delenv("GARMINTOKENS", raising=False)
    auth.session().reset()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the live tests unless opted in.

    This hook is handed every collected item, not just the ones beside this
    conftest, so it must filter by path -- otherwise it silently skips the
    whole offline suite and the run still looks green.
    """
    if os.getenv("GARMIN_LIVE") == "1":
        return
    here = Path(__file__).parent
    skip = pytest.mark.skip(reason="Set GARMIN_LIVE=1 to run tests against the real account.")
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)
