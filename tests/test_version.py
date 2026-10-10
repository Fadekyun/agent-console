"""Test that pyproject.toml and agent_console/__init__.py declare the same version."""

import ast
import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def _init_version() -> str:
    init_path = REPO_ROOT / "agent_console" / "__init__.py"
    tree = ast.parse(init_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets
        ):
            if isinstance(node.value, ast.Constant):
                return node.value.value
    raise AssertionError("Could not find __version__ assignment in agent_console/__init__.py")


def _ui_brand_version() -> str:
    html = (REPO_ROOT / "web" / "static" / "index.html").read_text(encoding="utf-8")
    match = re.search(r"<small>v(\d+\.\d+\.\d+)</small>", html)
    if not match:
        raise AssertionError("Could not find UI brand version in web/static/index.html")
    return match.group(1)


def test_versions_match() -> None:
    assert _pyproject_version() == _init_version()


def test_ui_brand_version_matches_package() -> None:
    assert _ui_brand_version() == _init_version()


# Regex for strict X.Y.Z release versions (no prerelease/build, no leading zeros).
_RELEASE_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def test_version_is_strict_release_semver() -> None:
    v = _pyproject_version()
    assert _RELEASE_VERSION_RE.match(v), (
        f"Version {v!r} must be strict X.Y.Z with no leading zeros"
    )
