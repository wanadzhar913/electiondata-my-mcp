from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from electiondata_my_mcp import prompt_loader
from electiondata_my_mcp.prompt_loader import (
    BUNDLED_PROMPT_PATH,
    _is_fresh,
    _write_atomically,
    default_cache_path,
    load_prompt,
    main,
)


def test_default_cache_path_uses_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    assert default_cache_path() == cache_root / "electiondata-my-mcp" / "query-builder-prompt.md"


def test_is_fresh_when_recent(tmp_path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("cached")
    assert _is_fresh(path, max_age=3600)


def test_is_fresh_when_missing(tmp_path) -> None:
    assert not _is_fresh(tmp_path / "missing.md", max_age=3600)


def test_write_atomically_writes_file(tmp_path) -> None:
    path = tmp_path / "nested" / "prompt.md"
    _write_atomically(path, "prompt text")
    assert path.read_text(encoding="utf-8") == "prompt text"


def test_load_prompt_uses_fresh_cache(tmp_path) -> None:
    cache = tmp_path / "prompt.md"
    cache.write_text("cached prompt", encoding="utf-8")
    assert load_prompt(cache_path=cache) == "cached prompt"


@patch("electiondata_my_mcp.prompt_loader.httpx.get")
def test_load_prompt_downloads_when_stale(mock_get: MagicMock, tmp_path) -> None:
    cache = tmp_path / "prompt.md"
    response = MagicMock()
    response.text = "downloaded prompt\n"
    response.raise_for_status = MagicMock()
    mock_get.return_value = response

    assert load_prompt(cache_path=cache, force_refresh=True) == "downloaded prompt\n"
    assert cache.read_text(encoding="utf-8") == "downloaded prompt\n"
    mock_get.assert_called_once()


@patch("electiondata_my_mcp.prompt_loader.httpx.get")
def test_load_prompt_falls_back_to_stale_cache(mock_get: MagicMock, tmp_path) -> None:
    cache = tmp_path / "prompt.md"
    cache.write_text("stale prompt", encoding="utf-8")
    mock_get.side_effect = httpx.ConnectError("offline")

    assert load_prompt(cache_path=cache, force_refresh=True) == "stale prompt"


@patch("electiondata_my_mcp.prompt_loader.httpx.get")
def test_load_prompt_falls_back_to_bundled_prompt(mock_get: MagicMock, tmp_path) -> None:
    mock_get.side_effect = httpx.ConnectError("offline")
    bundled = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")

    assert load_prompt(cache_path=tmp_path / "missing.md", force_refresh=True) == bundled


@patch("electiondata_my_mcp.prompt_loader.httpx.get")
def test_load_prompt_raises_when_no_source_available(mock_get: MagicMock, tmp_path) -> None:
    mock_get.side_effect = httpx.ConnectError("offline")
    missing = tmp_path / "missing.md"

    with patch.object(prompt_loader, "BUNDLED_PROMPT_PATH", tmp_path / "also-missing.md"):
        with pytest.raises(httpx.ConnectError):
            load_prompt(cache_path=missing, force_refresh=True)


@patch("electiondata_my_mcp.prompt_loader.httpx.get")
def test_load_prompt_empty_download_falls_back_to_bundled(mock_get: MagicMock, tmp_path) -> None:
    response = MagicMock()
    response.text = "   "
    response.raise_for_status = MagicMock()
    mock_get.return_value = response
    bundled = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")

    assert load_prompt(cache_path=tmp_path / "prompt.md", force_refresh=True) == bundled


def test_main_prints_prompt(tmp_path, capsys) -> None:
    cache = tmp_path / "prompt.md"
    cache.write_text("cli prompt", encoding="utf-8")

    with patch.object(
        sys,
        "argv",
        ["prompt_loader.py", "--cache-path", str(cache)],
    ):
        assert main() == 0

    captured = capsys.readouterr()
    assert "cli prompt" in captured.out


@patch("electiondata_my_mcp.prompt_loader.load_prompt", side_effect=httpx.ConnectError("offline"))
def test_main_returns_error_on_failure(_mock_load: MagicMock, capsys) -> None:
    with patch.object(sys, "argv", ["prompt_loader.py"]):
        assert main() == 1

    captured = capsys.readouterr()
    assert "Unable to load prompt" in captured.err
