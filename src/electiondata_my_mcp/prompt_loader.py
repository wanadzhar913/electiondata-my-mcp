#!/usr/bin/env python3
"""Download and cache the ElectionData.MY Query Builder prompt.

The prompt is loaded from the canonical raw GitHub URL and cached under the
user's cache directory. A stale cache is used when GitHub is unavailable.

Usage:
    python prompt_loader.py
    python prompt_loader.py --refresh
    python prompt_loader.py --cache-path /tmp/query-builder-prompt.md
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

PROMPT_URL = (
    "https://raw.githubusercontent.com/electiondata-my/meco-front/"
    "main/src/components/tools/query-builder/copy-prompt.md"
)
BUNDLED_PROMPT_PATH = Path(__file__).parent / "prompts" / "query-builder-prompt.md"
DEFAULT_MAX_AGE = 24 * 60 * 60  # 24 hours


def default_cache_path() -> Path:
    """Return the platform-appropriate cache path for the prompt."""
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
    return cache_root / "electiondata-my-mcp" / "query-builder-prompt.md"


def _is_fresh(path: Path, max_age: float) -> bool:
    return path.is_file() and time.time() - path.stat().st_mtime < max_age


def _write_atomically(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_prompt(
    *,
    cache_path: str | Path | None = None,
    max_age: float = DEFAULT_MAX_AGE,
    force_refresh: bool = False,
    timeout: float = 30.0,
) -> str:
    """Load the prompt, downloading it when the cache is absent or stale.

    If refreshing fails, a stale cache or the bundled prompt is returned.
    """
    path = Path(cache_path).expanduser() if cache_path else default_cache_path()

    if not force_refresh and _is_fresh(path, max_age):
        return path.read_text(encoding="utf-8")

    try:
        response = httpx.get(PROMPT_URL, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        prompt = response.text
        if not prompt.strip():
            raise ValueError("downloaded prompt is empty")
        _write_atomically(path, prompt)
        return prompt
    except (httpx.HTTPError, OSError, ValueError):
        if path.is_file():
            return path.read_text(encoding="utf-8")
        if BUNDLED_PROMPT_PATH.is_file():
            prompt = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")
            try:
                _write_atomically(path, prompt)
            except OSError:
                pass
            return prompt
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load and cache the ElectionData.MY Query Builder prompt."
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        help=f"cache destination (default: {default_cache_path()})",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_MAX_AGE,
        help="cache lifetime in seconds (default: 86400)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="download now even if the cache is fresh",
    )
    args = parser.parse_args()

    try:
        prompt = load_prompt(
            cache_path=args.cache_path,
            max_age=args.max_age,
            force_refresh=args.refresh,
        )
    except (httpx.HTTPError, OSError, ValueError) as error:
        print(f"Unable to load prompt: {error}", file=sys.stderr)
        return 1

    print(prompt, end="" if prompt.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
