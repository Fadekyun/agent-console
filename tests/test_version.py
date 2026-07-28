"""Test that pyproject.toml and agent_console/__init__.py declare the same version."""

import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def _init_version() -> str:
    init_text = (REPO_ROOT / "agent_console" / "__init__.py").read_text()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    assert m, "Could not find __version__ in agent_console/__init__.py"
    return m.group(1)


def test_versions_match() -> None:
    assert _pyproject_version() == _init_version()


def test_version_format() -> None:
    v = _pyproject_version()
    parts = v.split(".")
    assert len(parts) == 3, f"Expected semver (X.Y.Z), got {v}"
    for p in parts:
        assert p.isdigit(), f"Version part {p!r} is not numeric"
