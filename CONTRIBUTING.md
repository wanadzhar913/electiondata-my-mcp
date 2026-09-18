# Contributing to electiondata-my-mcp

Thanks for wanting to help. This is an unofficial MCP server that lets an LLM query the public ElectionData.MY [data lake](https://electiondata.my/data-catalogue/) with DuckDB SQL. It is not affiliated with the [ElectionData.MY](https://electiondata.my/) team.

Bug reports, dataset updates, validator fixes, docs, and tests are all welcome. If you are unsure whether a change belongs here, open an issue first.

## Code of conduct

Be respectful in issues, reviews, and discussions. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## What belongs in this repo

This server covers **the public Parquet lake only**. It needs no API key and no local copy of the data.

Good contributions:

- Keep `DATASETS` in sync when the lake or the [Query Builder `datasets.ts`](https://github.com/electiondata-my/meco-front/blob/main/src/components/tools/query-builder/datasets.ts) gains a table.
- Tighten the SQL safety rules, or fix a false positive in `query_validator.py`.
- Add or clarify MCP tools, resources, or the `build_election_query` prompt without changing the lake-only scope.
- Refresh the bundled Query Builder prompt when upstream `copy-prompt.md` changes in a way that matters offline.
- Tests, docs, and client-config examples (Claude Desktop, Claude Code, Cursor).

Usually out of scope:

- Mirroring or checking in lake Parquet files.
- Weakening the validator (DDL/DML, `read_parquet`, missing `LIMIT` on voter rolls) without a discussed design change.
- Renaming tables away from the Query Builder names. SQL written here should paste into [the site's Query Builder](https://electiondata.my/query-builder/) unchanged.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/wanadzhar913/electiondata-my-mcp.git
cd electiondata-my-mcp
uv sync --group dev
```

Run the checks you will see on a pull request:

```bash
uv run ruff check
uv run pytest -q
```

That pytest run is the **unit** suite (mocked DuckDB / httpx, no network). Coverage must stay at **80%** (`--cov-fail-under=80` in `pyproject.toml`). CI runs it on Ubuntu, macOS, and Windows against Python 3.11, 3.12, and 3.13.

To exercise the live stdio server against `lake.electiondata.my`:

```bash
uv run pytest -m integration --no-cov
```

`--no-cov` is required: a handful of live tests will not meet the 80% gate. These tests spawn `python -m electiondata_my_mcp` and query `headline_stats` over HTTP.

Exercise the server with the [MCP Inspector](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector):

```bash
uv run mcp dev src/electiondata_my_mcp/server.py
```

To point a desktop client at your checkout instead of PyPI, see [From a local checkout](README.md#from-a-local-checkout) in the README.

You can also run SQL against the lake without MCP:

```bash
uv run src/electiondata_my_mcp/duckdb_lake.py --tables
uv run src/electiondata_my_mcp/duckdb_lake.py \
    "SELECT seat, majority FROM headline_stats ORDER BY majority DESC LIMIT 5"
```

## Layout

| Path | Role |
| --- | --- |
| `src/electiondata_my_mcp/server.py` | MCP tools, resource, and prompt. |
| `src/electiondata_my_mcp/duckdb_lake.py` | Table → Parquet URL map, `connect()`, CLI. |
| `src/electiondata_my_mcp/query_validator.py` | Read-only SQL allowlist used by `validate_sql` / `execute_query`. |
| `src/electiondata_my_mcp/prompt_loader.py` | Fetches and caches the Query Builder guide. |
| `src/electiondata_my_mcp/prompts/query-builder-prompt.md` | Bundled fallback if GitHub is unreachable. |
| `tests/` | Pytest suite. `@pytest.mark.unit` (mocked; default CI). `@pytest.mark.integration` is opt-in (live stdio + lake). |

## Common changes

### New or renamed lake table

1. Confirm the name and URL in upstream [`datasets.ts`](https://github.com/electiondata-my/meco-front/blob/main/src/components/tools/query-builder/datasets.ts). That file is the source of truth.
2. Add or update the entry in `DATASETS` in `duckdb_lake.py`.
3. Voter rolls stay streamed automatically if the name starts with `voter_roll_` (`LAZY`). Do not materialise them.
4. One-off tables (not `saluran_*` / `voter_roll_*`) need a human description in `DATASET_DESCRIPTIONS` in `server.py`.
5. Update tests that hard-code the dataset count — `test_list_datasets_includes_known_table` currently expects 27 tables.

### Validator or query safety

Keep the existing contract unless you are explicitly changing it, and cover the change in `tests/test_query_validator.py`:

- `SELECT` or `WITH` only, one statement.
- No DDL, DML, or session keywords (`CREATE`, `ATTACH`, `INSTALL`, `PRAGMA`, `SET`, …).
- No file or network functions (`read_parquet`, `read_csv`, `glob`, …). Allowlisted views are the only reachable data.
- At least one known lake table.
- Any query that touches `voter_roll_*` must include `LIMIT 10000` or less.

`execute_query` still returns at most 100 rows by default and 1,000 at the ceiling.

### Query Builder prompt

`electiondata://query-guide` is loaded from upstream `copy-prompt.md`, cached for 24 hours under `$XDG_CACHE_HOME/electiondata-my-mcp/`, then the bundled copy. If you change fetch, cache, or fallback behaviour, update `tests/test_prompt_loader.py` and consider refreshing `src/electiondata_my_mcp/prompts/query-builder-prompt.md` so offline users see current schema notes.

## Pull requests

1. Fork the repo and create a branch from `main` (`git checkout -b fix/short-description`).
2. Make a focused change. Match the surrounding style; `ruff` is the linter (`line-length = 100`, Python 3.11).
3. Add or update tests. Prefer the existing mocks in `tests/conftest.py` over live lake calls. Every test must be marked `unit` or `integration`. Live stdio/lake coverage lives in `tests/test_live_lake.py` and `tests/test_live_mcp_client.py` and stays behind `pytest -m integration`.
4. For user-facing changes, add a note under the next version in `CHANGELOG.md`. Leave the version in `pyproject.toml` and `__init__.py` alone unless a maintainer asks you to bump it.
5. Open a pull request against `main`. Describe the problem, the approach, and how you tested it.

Please do not commit `.env`, credentials, or local DuckDB cache files.

## Releases

Maintainers publish from a GitHub Release whose tag (`vX.Y.Z`) matches `version` in `pyproject.toml`. The [publish workflow](.github/workflows/publish.yml) runs the test matrix, then uploads to PyPI via trusted publishing.

## Questions

Open an issue at [wanadzhar913/electiondata-my-mcp](https://github.com/wanadzhar913/electiondata-my-mcp/issues). Prefix the title with `[Question]`, `[Bug]`, or `[Feature]` when that helps.

This project is dedicated to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
