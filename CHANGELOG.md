# Changelog

## Unreleased

- Enrich `list_datasets` and `describe_dataset` with coverage dates, `use_for` hints, and pointers to `get_query_guide`. Add `dataset_catalog.py` as the metadata source for discovery tools.
- `validate_sql` / `execute_query` warn when a state-election filter (`election = 'SE-*'`) lacks a `state` filter on headline or demographics tables.
- Add a production Dockerfile for Streamable HTTP (`electiondata_my_mcp.http_app:app`, four uvicorn workers by default via `WEB_CONCURRENCY`, non-root UID 10001, `/health` `HEALTHCHECK`, Python and uv pinned to the same versions in both stages) and document `docker build` / `docker run` in `docs/PRODUCTION.md`.
- Add pre-commit hooks for Ruff (lint and format) and basic file checks. CI runs `pre-commit run --all-files`.
- Optional OAuth 2.1 resource-server protection for Streamable HTTP. Set `MCP_OAUTH_ISSUER_URL`, `MCP_OAUTH_RESOURCE_URL`, and `MCP_OAUTH_JWKS_URL` to require a JWT access token on `/mcp`. For Auth0, the issuer is the tenant origin with a trailing slash (`https://your-tenant.us.auth0.com/`), not the `/oauth/token` URL. The resource URL is the API identifier, including `/mcp` (`http://127.0.0.1:8000/mcp` locally, or `https://mcp.example.com/mcp` in production). Unset, stdio, and `GET /health` stay open.

## 0.2.0 — 2026-09-19

- Optional Streamable HTTP transport via `mcp.streamable_http_app()`, CORS, DNS-rebinding allowlists, `GET /health`, and uvicorn `--workers`.
- Stdio remains the default; HTTP flags are rejected unless `--transport streamable-http`.
- Concurrent lake queries share one DuckDB database per process via a bounded cursor pool (`MCP_DUCKDB_POOL_SIZE` / `MCP_DUCKDB_POOL_TIMEOUT`).
- Deploy notes live in `docs/PRODUCTION.md`.
- Add PyPI package downloads badge from https://pepy.tech.
- Mark the mocked pytest suite as `unit` (default, CI) and add opt-in `integration` tests that spawn the real stdio MCP server against `lake.electiondata.my`.
- Pull-request CI stays unit-only; maintainers run the live suite from Actions (`workflow_dispatch`) or the `run-integration` PR label.

## 0.1.1 — 2026-09-11

Documentation-only. No runtime changes.

- Publish the uvx / PyPI client setup for Claude Desktop, Claude Code, and Cursor on the package page.
- Move git-clone stdio configs under Development.
- Point project URLs at `wanadzhar913/electiondata-my-mcp`.

## 0.1.0 — 2026-09-10

- Initial PyPI release.
