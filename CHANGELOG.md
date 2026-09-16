# Changelog

## 0.2.0 — 2026-09-16

- Optional Streamable HTTP transport via `mcp.streamable_http_app()`, CORS, DNS-rebinding allowlists, `GET /health`, and uvicorn `--workers`.
- Stdio remains the default; HTTP flags are rejected unless `--transport streamable-http`.
- Concurrent lake queries share one DuckDB database per process via a bounded cursor pool (`MCP_DUCKDB_POOL_SIZE` / `MCP_DUCKDB_POOL_TIMEOUT`).
- Deploy notes live in `PRODUCTION.md`.
- Add PyPI package downloads badge from https://pepy.tech.

## 0.1.1 — 2026-09-11

Documentation-only. No runtime changes.

- Publish the uvx / PyPI client setup for Claude Desktop, Claude Code, and Cursor on the package page.
- Move git-clone stdio configs under Development.
- Point project URLs at `wanadzhar913/electiondata-my-mcp`.

## 0.1.0 — 2026-09-10

- Initial PyPI release.
