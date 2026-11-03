from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

import check_upstream_drift
from check_upstream_drift import (
    MCP_ONLY_TABLES,
    AllowlistError,
    DatasetsParseError,
    build_report,
    compare_datasets,
    diff_prompt,
    main,
    parse_datasets_ts,
    render_report,
    resolve_allowlist,
)
from electiondata_my_mcp.duckdb_lake import DATASETS
from electiondata_my_mcp.prompt_loader import BUNDLED_PROMPT_PATH, PROMPT_URL

pytestmark = pytest.mark.unit

_KEPT = "https://lake.electiondata.my/results_headline/headline_ballots.parquet"
_NEW = "https://lake.electiondata.my/results_headline/lookup_candidate.parquet"
_OLD = "https://lake.electiondata.my/results_headline/headline_stats.parquet"


def _datasets_ts(datasets: dict[str, str]) -> str:
    rows = ["export const DATASETS = {"]
    rows.extend(f'  {name}: "{url}",' for name, url in datasets.items())
    rows.append("} as const;")
    rows.append('export const DATASET_LABELS = { headline_ballots: "headline_ballots" };')
    return "\n".join(rows) + "\n"


def test_prompt_url_tracks_the_same_meco_front_ref() -> None:
    assert PROMPT_URL == check_upstream_drift.raw_github_url(check_upstream_drift.PROMPT_PATH)
    assert check_upstream_drift.DATASETS_URL.endswith(
        "electiondata-my/meco-front/main/src/components/tools/query-builder/datasets.ts"
    )


def test_mcp_only_tables_start_empty() -> None:
    assert MCP_ONLY_TABLES == frozenset()


def test_parse_datasets_ignores_comments_labels_and_lazy_set() -> None:
    source = """
export type DatasetKey = keyof typeof DATASETS;

// export const DATASETS = { fake: "https://example.com/no.parquet" };
const note = "export const DATASETS = { fake: \\"https://example.com/no.parquet\\" }";

export const DATASETS: Record<string, string> = {
  headline_ballots: "https://lake.electiondata.my/results_headline/headline_ballots.parquet",
  // ignored: "https://example.com/ignored.parquet",
  /* { voter_roll_ge15: "https://example.com/block.parquet" } */
  'headline_stats': 'https://lake.electiondata.my/results_headline/headline_stats.parquet',
  `saluran_ballots_ge15`: `https://lake.electiondata.my/results_saluran/ge15_ballots.parquet`,
} as const;

export const LAZY_DATASETS = new Set<DatasetKey>([
  "voter_roll_ge15",
]);
"""
    assert parse_datasets_ts(source) == {
        "headline_ballots": "https://lake.electiondata.my/results_headline/headline_ballots.parquet",
        "headline_stats": "https://lake.electiondata.my/results_headline/headline_stats.parquet",
        "saluran_ballots_ge15": "https://lake.electiondata.my/results_saluran/ge15_ballots.parquet",
    }


def test_parse_datasets_accepts_assignment_without_space() -> None:
    source = 'export const DATASETS={"headline_ballots":"https://lake.electiondata.my/a.parquet"}'
    assert parse_datasets_ts(source) == {
        "headline_ballots": "https://lake.electiondata.my/a.parquet"
    }


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("export const OTHER = {};", "no `export const DATASETS`"),
        (
            'export const DATASETS = { headline_ballots: "https://lake.electiondata.my/a.parquet",',
            "unterminated",
        ),
        (
            """
            export const DATASETS = {
              headline_ballots: "https://lake.electiondata.my/a.parquet",
              headline_ballots: "https://lake.electiondata.my/b.parquet",
            };
            """,
            "duplicate dataset name",
        ),
        (
            "export const DATASETS = { headline_ballots: 1 };",
            "expected a quoted string",
        ),
        (
            "export const DATASETS = { "
            '"headline-ballots": "https://lake.electiondata.my/a.parquet" };',
            "unsupported dataset name",
        ),
        (
            'export const DATASETS = { headline_ballots: "ftp://lake.electiondata.my/a.parquet" };',
            "dataset URL must be an http",
        ),
        (
            """
            export const DATASETS = {
              headline_ballots: "https://lake.electiondata.my/a.parquet"
              headline_stats: "https://lake.electiondata.my/b.parquet",
            };
            """,
            "expected ','",
        ),
    ],
)
def test_parse_datasets_rejects_unusable_input(source: str, message: str) -> None:
    with pytest.raises(DatasetsParseError, match=message):
        parse_datasets_ts(source)


def test_compare_datasets_reports_missing_mismatch_and_unexpected() -> None:
    drift = compare_datasets(
        {"kept": _KEPT, "added": _NEW, "moved": _NEW},
        {"kept": _KEPT, "moved": _OLD, "extra": _OLD},
    )

    assert drift.ok is False
    assert drift.missing_from_mcp == (("added", _NEW),)
    assert drift.url_mismatch == (("moved", _NEW, _OLD),)
    assert drift.unexpected_mcp_only == (("extra", _OLD),)
    assert drift.matched_count == 1
    assert drift.ignored_mcp_only == ()


def test_compare_datasets_ignores_allowlisted_mcp_only_tables() -> None:
    drift = compare_datasets(
        {"kept": _KEPT},
        {"kept": _KEPT, "lookup_candidate": _NEW},
        mcp_only_allowlist=["lookup_candidate"],
    )

    assert drift.ok is True
    assert drift.ignored_mcp_only == ("lookup_candidate",)
    assert drift.unexpected_mcp_only == ()


def test_compare_datasets_flags_stale_allowlist_entries() -> None:
    absent = compare_datasets({"kept": _KEPT}, {"kept": _KEPT}, mcp_only_allowlist=["lookup_party"])
    published = compare_datasets(
        {"kept": _KEPT, "lookup_party": _NEW},
        {"kept": _KEPT, "lookup_party": _NEW},
        mcp_only_allowlist=["lookup_party"],
    )

    assert absent.ok is False
    assert absent.stale_allowlist == (("lookup_party", "not registered in DATASETS"),)
    assert published.ok is False
    assert published.stale_allowlist == (
        ("lookup_party", "also published in datasets.ts; remove it from MCP_ONLY_TABLES"),
    )
    assert published.url_mismatch == ()

    upstream_only = compare_datasets(
        {"kept": _KEPT, "lookup_party": _NEW},
        {"kept": _KEPT},
        mcp_only_allowlist=["lookup_party"],
    )
    assert upstream_only.missing_from_mcp == (("lookup_party", _NEW),)
    assert upstream_only.stale_allowlist == (
        ("lookup_party", "missing from DATASETS; datasets.ts already lists it"),
    )


def test_diff_prompt_ignores_newline_style_only() -> None:
    assert diff_prompt("alpha\r\nbeta\r\n", "alpha\nbeta\n") == ""


def test_diff_prompt_is_a_unified_diff() -> None:
    diff = diff_prompt("alpha\n", "alpha\nbeta\n")
    assert diff.startswith("--- src/electiondata_my_mcp/prompts/query-builder-prompt.md\n")
    assert (
        "+++ electiondata-my/meco-front/src/components/tools/query-builder/copy-prompt.md\n" in diff
    )
    assert "+beta\n" in diff


def test_render_report_lists_dataset_and_prompt_drift() -> None:
    report = build_report(
        datasets_ts=_datasets_ts({"kept": _KEPT, "added": _NEW, "moved": _NEW}),
        upstream_prompt="upstream guide\n",
        mcp_datasets={"kept": _KEPT, "moved": _OLD, "extra": _OLD},
        bundled_prompt="bundled guide\n",
        mcp_only_allowlist=["missing_lookup"],
    )

    text = render_report(report)
    assert text.startswith("Upstream drift detected against electiondata-my/meco-front main.")
    assert "+ added" in text
    assert "~ moved" in text
    assert "- extra" in text
    assert "! missing_lookup" in text
    assert "Query guide differs." in text
    assert "-bundled guide" in text
    assert "+upstream guide" in text


def test_current_mirror_matches_a_round_trip_of_itself() -> None:
    bundled = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")
    report = build_report(
        datasets_ts=_datasets_ts(dict(DATASETS)),
        upstream_prompt=bundled,
        mcp_datasets=DATASETS,
        bundled_prompt=bundled,
        mcp_only_allowlist=MCP_ONLY_TABLES,
    )

    assert report.ok is True
    assert report.datasets.matched_count == len(DATASETS)
    assert render_report(report).startswith("No upstream drift against")
    assert "MCP-only tables ignored: none" in render_report(report)


def test_resolve_allowlist_rejects_non_identifiers() -> None:
    with pytest.raises(AllowlistError, match="invalid MCP-only table name"):
        resolve_allowlist(["lookup-candidate"])


def test_main_succeeds_when_fetch_matches_the_mirror(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    bundled = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")
    payloads = {
        check_upstream_drift.DATASETS_URL: _datasets_ts(dict(DATASETS)),
        PROMPT_URL: bundled,
    }

    def fake_fetch(url: str, *, timeout: float) -> str:
        assert timeout == 30.0
        return payloads[url]

    monkeypatch.setattr(check_upstream_drift, "fetch_text", fake_fetch)

    assert main([]) == 0
    assert "No upstream drift against electiondata-my/meco-front main." in capsys.readouterr().out


def test_main_allow_mcp_only_hides_an_extra_table(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    bundled = BUNDLED_PROMPT_PATH.read_text(encoding="utf-8")
    name = next(iter(DATASETS))
    upstream = {key: url for key, url in DATASETS.items() if key != name}
    payloads = {
        check_upstream_drift.DATASETS_URL: _datasets_ts(upstream),
        PROMPT_URL: bundled,
    }
    monkeypatch.setattr(check_upstream_drift, "fetch_text", lambda url, *, timeout: payloads[url])

    assert main([]) == 1
    assert f"- {name}" in capsys.readouterr().out

    assert main(["--allow-mcp-only", name]) == 0
    captured = capsys.readouterr().out
    assert f"MCP-only tables ignored: {name}" in captured


def test_main_reports_fetch_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def boom(url: str, *, timeout: float) -> str:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(check_upstream_drift, "fetch_text", boom)

    assert main([]) == 1
    assert "offline" in capsys.readouterr().err


def test_main_reports_parse_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    payloads = {
        check_upstream_drift.DATASETS_URL: "not datasets",
        PROMPT_URL: "prompt",
    }
    monkeypatch.setattr(check_upstream_drift, "fetch_text", lambda url, *, timeout: payloads[url])

    assert main([]) == 1
    assert "no `export const DATASETS`" in capsys.readouterr().err


def test_fetch_text_returns_utf8_body(monkeypatch: pytest.MonkeyPatch) -> None:
    response = MagicMock()
    response.text = "hello"
    response.raise_for_status = MagicMock()
    monkeypatch.setattr(check_upstream_drift.httpx, "get", lambda *args, **kwargs: response)

    assert check_upstream_drift.fetch_text("https://example.com/datasets.ts", timeout=1) == "hello"
    response.raise_for_status.assert_called_once()
    assert response.encoding == "utf-8"
