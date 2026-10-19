"""Release metadata that must agree, or the registry or PyPI rejects the release."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import garmin_api

ROOT = Path(__file__).resolve().parent.parent


def test_versions_agree() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    server = json.loads((ROOT / "server.json").read_text())
    assert (
        project == server["version"] == server["packages"][0]["version"] == garmin_api.__version__
    )


def test_the_registry_can_verify_ownership() -> None:
    """The registry reads this line from the README that PyPI displays."""
    name = json.loads((ROOT / "server.json").read_text())["name"]
    assert f"mcp-name: {name}" in (ROOT / "README.md").read_text()


def test_registry_description_fits() -> None:
    """The registry rejects a description over 100 characters."""
    assert len(json.loads((ROOT / "server.json").read_text())["description"]) <= 100


def test_the_package_command_starts_the_server() -> None:
    """``uvx garmin-connect-api`` runs the command named like the package."""
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["garmin-connect-api"] == "garmin_api.cli:mcp_entry"


def test_readme_links_work_on_pypi() -> None:
    """PyPI renders the README away from the repo, so relative links break there."""
    readme = (ROOT / "README.md").read_text()
    relative = re.findall(r"\]\((?!https?://|#)([^)]+)\)", readme)
    assert relative == []
