from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from electiondata_my_mcp import __version__

pytestmark = pytest.mark.unit


def test_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == expected
