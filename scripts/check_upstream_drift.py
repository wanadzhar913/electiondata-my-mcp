#!/usr/bin/env python3
"""Fail when this MCP drifts from the ElectionData.MY Query Builder sources.

Fetches these files from electiondata-my/meco-front ``main``:

- ``src/components/tools/query-builder/datasets.ts``
  compared to ``DATASETS`` in ``electiondata_my_mcp.duckdb_lake`` (table name and URL)
- ``src/components/tools/query-builder/copy-prompt.md``
  compared to the bundled ``prompts/query-builder-prompt.md``

``MCP_ONLY_TABLES`` lists tables this server registers that ``datasets.ts`` does
not. Those names are intentional extensions and stay out of the drift report.
Drop a name once ``datasets.ts`` publishes the same table. A name that is
absent from ``DATASETS`` fails the check.

CRLF and CR are treated as LF before the prompt is compared. Every other
prompt byte is compared verbatim.

Exit status is 0 when the mirror matches, and 1 when it differs or a file
cannot be fetched or parsed.

Usage:
    uv run python scripts/check_upstream_drift.py
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass

import httpx

from electiondata_my_mcp.duckdb_lake import DATASETS
from electiondata_my_mcp.prompt_loader import BUNDLED_PROMPT_PATH, PROMPT_URL

UPSTREAM_REPO = "electiondata-my/meco-front"
UPSTREAM_REF = "main"
DATASETS_PATH = "src/components/tools/query-builder/datasets.ts"
PROMPT_PATH = "src/components/tools/query-builder/copy-prompt.md"

BUNDLED_PROMPT_LABEL = "src/electiondata_my_mcp/prompts/query-builder-prompt.md"
MCP_DATASETS_LABEL = "src/electiondata_my_mcp/duckdb_lake.py DATASETS"
UPSTREAM_DATASETS_LABEL = f"{UPSTREAM_REPO}/{DATASETS_PATH}"
UPSTREAM_PROMPT_LABEL = f"{UPSTREAM_REPO}/{PROMPT_PATH}"

# Tables this MCP registers that meco-front datasets.ts does not.
# A later lookup_* addition (meco-etl, not the Query Builder) belongs here.
MCP_ONLY_TABLES: frozenset[str] = frozenset()

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGNMENT = re.compile(r"export\s+const\s+DATASETS\b(?:\s*:\s*[^=]+?)?\s*=")
_USER_AGENT = "electiondata-my-mcp-upstream-drift"


class DatasetsParseError(ValueError):
    """``datasets.ts`` could not be parsed into a name to URL map."""


class AllowlistError(ValueError):
    """An MCP-only allowlist entry is not a table identifier."""


def raw_github_url(path: str) -> str:
    """Return the raw.githubusercontent.com URL for ``path`` on meco-front main."""
    return f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_REF}/{path}"


DATASETS_URL = raw_github_url(DATASETS_PATH)


@dataclass(frozen=True)
class DatasetDrift:
    """Difference between upstream ``DATASETS`` and this MCP's table map."""

    upstream_count: int
    mcp_count: int
    matched_count: int
    missing_from_mcp: tuple[tuple[str, str], ...]
    url_mismatch: tuple[tuple[str, str, str], ...]
    unexpected_mcp_only: tuple[tuple[str, str], ...]
    ignored_mcp_only: tuple[str, ...]
    stale_allowlist: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return not (
            self.missing_from_mcp
            or self.url_mismatch
            or self.unexpected_mcp_only
            or self.stale_allowlist
        )


@dataclass(frozen=True)
class DriftReport:
    """Dataset map and query-guide comparison."""

    datasets: DatasetDrift
    prompt_diff: str

    @property
    def ok(self) -> bool:
        return self.datasets.ok and self.prompt_diff == ""


def fetch_text(url: str, *, timeout: float) -> str:
    """GET ``url`` and return the body as UTF-8 text."""
    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/plain, text/markdown, */*"},
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_datasets_ts(source: str) -> dict[str, str]:
    """Return the ``export const DATASETS`` object as a name to URL map.

    Labels, descriptions, and ``LAZY_DATASETS`` elsewhere in the file are ignored.
    """
    body = _object_body(source, _assignment_end(source))
    return _parse_entries(body)


def compare_datasets(
    upstream: Mapping[str, str],
    mcp: Mapping[str, str],
    *,
    mcp_only_allowlist: Collection[str] = (),
) -> DatasetDrift:
    """Compare upstream table URLs with this MCP.

    Names in ``mcp_only_allowlist`` that exist only on the MCP side are ignored.
    Any other allowlist name is reported as stale.
    """
    allow = set(mcp_only_allowlist)
    missing = tuple(sorted((name, url) for name, url in upstream.items() if name not in mcp))
    mismatch = tuple(
        sorted(
            (name, upstream[name], mcp[name])
            for name in upstream.keys() & mcp.keys()
            if upstream[name] != mcp[name]
        )
    )
    unexpected = tuple(
        sorted(
            (name, url) for name, url in mcp.items() if name not in upstream and name not in allow
        )
    )
    ignored = tuple(sorted(name for name in mcp if name not in upstream and name in allow))
    stale = tuple(
        sorted(
            (name, reason)
            for name in allow
            if (reason := _stale_reason(name, upstream, mcp)) is not None
        )
    )
    matched = sum(1 for name in upstream.keys() & mcp.keys() if upstream[name] == mcp[name])
    return DatasetDrift(
        upstream_count=len(upstream),
        mcp_count=len(mcp),
        matched_count=matched,
        missing_from_mcp=missing,
        url_mismatch=mismatch,
        unexpected_mcp_only=unexpected,
        ignored_mcp_only=ignored,
        stale_allowlist=stale,
    )


def diff_prompt(bundled: str, upstream: str) -> str:
    """Return a unified diff of the bundled guide against upstream, or ``""``."""
    left = _normalize_newlines(bundled)
    right = _normalize_newlines(upstream)
    if left == right:
        return ""
    return "".join(
        difflib.unified_diff(
            left.splitlines(keepends=True),
            right.splitlines(keepends=True),
            fromfile=BUNDLED_PROMPT_LABEL,
            tofile=UPSTREAM_PROMPT_LABEL,
        )
    )


def build_report(
    *,
    datasets_ts: str,
    upstream_prompt: str,
    mcp_datasets: Mapping[str, str],
    bundled_prompt: str,
    mcp_only_allowlist: Collection[str],
) -> DriftReport:
    """Compare fetched upstream text with this MCP's mirror."""
    return DriftReport(
        datasets=compare_datasets(
            parse_datasets_ts(datasets_ts),
            mcp_datasets,
            mcp_only_allowlist=mcp_only_allowlist,
        ),
        prompt_diff=diff_prompt(bundled_prompt, upstream_prompt),
    )


def render_report(report: DriftReport) -> str:
    """Render a drift report for stdout and CI logs."""
    header = (
        f"No upstream drift against {UPSTREAM_REPO} {UPSTREAM_REF}."
        if report.ok
        else f"Upstream drift detected against {UPSTREAM_REPO} {UPSTREAM_REF}."
    )
    return "\n".join(
        [
            header,
            "",
            _render_datasets(report.datasets),
            "",
            _render_prompt(report.prompt_diff),
        ]
    )


def resolve_allowlist(extra: list[str]) -> frozenset[str]:
    """Combine ``MCP_ONLY_TABLES`` with comma-separated CLI names."""
    names = set(MCP_ONLY_TABLES)
    for item in extra:
        for part in item.split(","):
            name = part.strip()
            if name:
                names.add(name)
    invalid = sorted(name for name in names if _IDENT.fullmatch(name) is None)
    if invalid:
        joined = ", ".join(repr(name) for name in invalid)
        raise AllowlistError(f"invalid MCP-only table name(s): {joined}")
    return frozenset(names)


def main(argv: list[str] | None = None) -> int:
    """Fetch meco-front main, print the report, and return the exit status."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare this MCP's lake table map and bundled Query Builder guide "
            f"with {UPSTREAM_REPO} {UPSTREAM_REF}."
        )
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for each upstream file (default: 30)",
    )
    parser.add_argument(
        "--allow-mcp-only",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Extra MCP-only table to ignore, in addition to MCP_ONLY_TABLES. "
            "Repeat the flag or pass a comma-separated list."
        ),
    )
    args = parser.parse_args(argv)

    try:
        allowlist = resolve_allowlist(args.allow_mcp_only)
        datasets_ts = fetch_text(DATASETS_URL, timeout=args.timeout)
        upstream_prompt = fetch_text(PROMPT_URL, timeout=args.timeout)
        bundled_prompt = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")
        report = build_report(
            datasets_ts=datasets_ts,
            upstream_prompt=upstream_prompt,
            mcp_datasets=DATASETS,
            bundled_prompt=bundled_prompt,
            mcp_only_allowlist=allowlist,
        )
    except (httpx.HTTPError, OSError, DatasetsParseError, AllowlistError) as exc:
        print(f"Upstream drift check failed: {exc}", file=sys.stderr)
        return 1

    print(render_report(report))
    return 0 if report.ok else 1


def _stale_reason(
    name: str,
    upstream: Mapping[str, str],
    mcp: Mapping[str, str],
) -> str | None:
    in_mcp = name in mcp
    in_upstream = name in upstream
    if in_mcp and not in_upstream:
        return None
    if not in_mcp and not in_upstream:
        return "not registered in DATASETS"
    if not in_mcp and in_upstream:
        return "missing from DATASETS; datasets.ts already lists it"
    return "also published in datasets.ts; remove it from MCP_ONLY_TABLES"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _skip_line_comment(source: str, index: int) -> int:
    end = source.find("\n", index)
    return len(source) if end < 0 else end + 1


def _skip_block_comment(source: str, index: int) -> int:
    end = source.find("*/", index + 2)
    if end < 0:
        raise DatasetsParseError("unterminated block comment in datasets.ts")
    return end + 2


def _skip_string(source: str, index: int) -> int:
    quote = source[index]
    index += 1
    limit = len(source)
    while index < limit:
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index + 1
        index += 1
    raise DatasetsParseError("unterminated string in datasets.ts")


def _skip_ws_and_comments(source: str, index: int) -> int:
    limit = len(source)
    while index < limit:
        if source[index] in " \t\r\n":
            index += 1
            continue
        if source.startswith("//", index):
            index = _skip_line_comment(source, index)
            continue
        if source.startswith("/*", index):
            index = _skip_block_comment(source, index)
            continue
        break
    return index


def _assignment_end(source: str) -> int:
    index = 0
    limit = len(source)
    while index < limit:
        if source.startswith("//", index):
            index = _skip_line_comment(source, index)
            continue
        if source.startswith("/*", index):
            index = _skip_block_comment(source, index)
            continue
        if source[index] in "\"'`":
            index = _skip_string(source, index)
            continue
        match = _ASSIGNMENT.match(source, index)
        if match:
            return match.end()
        index += 1
    raise DatasetsParseError("datasets.ts has no `export const DATASETS` object")


def _object_body(source: str, start: int) -> str:
    index = _skip_ws_and_comments(source, start)
    if index >= len(source) or source[index] != "{":
        raise DatasetsParseError("DATASETS is not an object literal")
    close = _matching_brace(source, index)
    return source[index + 1 : close]


def _matching_brace(source: str, open_index: int) -> int:
    depth = 0
    index = open_index
    limit = len(source)
    while index < limit:
        if source.startswith("//", index):
            index = _skip_line_comment(source, index)
            continue
        if source.startswith("/*", index):
            index = _skip_block_comment(source, index)
            continue
        char = source[index]
        if char in "\"'`":
            index = _skip_string(source, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise DatasetsParseError("DATASETS object is unterminated")


def _parse_quoted(source: str, index: int) -> tuple[str, int]:
    if index >= len(source) or source[index] not in "\"'`":
        snippet = source[index : index + 40]
        raise DatasetsParseError(f"expected a quoted string, found {snippet!r}")
    end = _skip_string(source, index)
    raw = source[index + 1 : end - 1]
    if "\\" in raw:
        raise DatasetsParseError(f"escaped characters are not supported in {raw!r}")
    return raw, end


def _parse_key(source: str, index: int) -> tuple[str, int]:
    if index < len(source) and source[index] in "\"'`":
        name, end = _parse_quoted(source, index)
        if _IDENT.fullmatch(name) is None:
            raise DatasetsParseError(f"unsupported dataset name {name!r}")
        return name, end
    match = _IDENT.match(source, index)
    if not match:
        snippet = source[index : index + 40]
        raise DatasetsParseError(f"expected a dataset name, found {snippet!r}")
    return match.group(0), match.end()


def _parse_url(source: str, index: int) -> tuple[str, int]:
    url, end = _parse_quoted(source, index)
    if any(char.isspace() for char in url) or not url.startswith(("http://", "https://")):
        raise DatasetsParseError(f"dataset URL must be an http(s) URL, found {url!r}")
    return url, end


def _parse_entries(body: str) -> dict[str, str]:
    datasets: dict[str, str] = {}
    index = 0
    limit = len(body)
    while True:
        index = _skip_ws_and_comments(body, index)
        if index >= limit:
            return datasets
        name, index = _parse_key(body, index)
        index = _skip_ws_and_comments(body, index)
        if index >= limit or body[index] != ":":
            raise DatasetsParseError(f"expected ':' after dataset name {name!r}")
        index = _skip_ws_and_comments(body, index + 1)
        url, index = _parse_url(body, index)
        if name in datasets:
            raise DatasetsParseError(f"duplicate dataset name {name!r} in DATASETS")
        datasets[name] = url
        index = _skip_ws_and_comments(body, index)
        if index >= limit:
            return datasets
        if body[index] != ",":
            snippet = body[index : index + 40]
            raise DatasetsParseError(
                f"expected ',' or end of DATASETS after {name!r}, found {snippet!r}"
            )
        index += 1


def _render_datasets(drift: DatasetDrift) -> str:
    if drift.ok:
        header = f"Datasets match ({drift.upstream_count} upstream tables)."
    else:
        header = (
            "Datasets differ "
            f"(upstream {drift.upstream_count}, mcp {drift.mcp_count}, "
            f"matching {drift.matched_count})."
        )
    lines = [header, f"  {UPSTREAM_DATASETS_LABEL}", f"  {MCP_DATASETS_LABEL}"]
    if drift.missing_from_mcp:
        lines.append("")
        lines.append("Missing from this MCP:")
        for name, url in drift.missing_from_mcp:
            lines.append(f"  + {name}")
            lines.append(f"      {url}")
    if drift.url_mismatch:
        lines.append("")
        lines.append("URL mismatch:")
        for name, upstream_url, mcp_url in drift.url_mismatch:
            lines.append(f"  ~ {name}")
            lines.append(f"      upstream: {upstream_url}")
            lines.append(f"      mcp:      {mcp_url}")
    if drift.unexpected_mcp_only:
        lines.append("")
        lines.append(
            "Unexpected MCP-only tables "
            "(add the name to MCP_ONLY_TABLES if this server should keep it):"
        )
        for name, url in drift.unexpected_mcp_only:
            lines.append(f"  - {name}")
            lines.append(f"      {url}")
    if drift.stale_allowlist:
        lines.append("")
        lines.append("MCP_ONLY_TABLES entries that are not MCP-only:")
        for name, reason in drift.stale_allowlist:
            lines.append(f"  ! {name}")
            lines.append(f"      {reason}")
    ignored = ", ".join(drift.ignored_mcp_only) if drift.ignored_mcp_only else "none"
    lines.append("")
    lines.append(f"MCP-only tables ignored: {ignored}")
    return "\n".join(lines)


def _render_prompt(prompt_diff: str) -> str:
    lines = [
        "Query guide matches." if prompt_diff == "" else "Query guide differs.",
        f"  {UPSTREAM_PROMPT_LABEL}",
        f"  {BUNDLED_PROMPT_LABEL}",
    ]
    if prompt_diff:
        lines.append("")
        lines.append(prompt_diff.rstrip("\n"))
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
