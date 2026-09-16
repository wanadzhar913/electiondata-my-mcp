# Production: Streamable HTTP

Stdio is the default transport and is what desktop clients (`uvx electiondata-my-mcp`) speak. This page is the HTTP deploy path: a Starlette app from `mcp.streamable_http_app()`, CORS, DNS-rebinding protection, a `/health` route, and uvicorn workers.

`mcp.run("streamable-http")` is not used here. That helper starts a single in-process uvicorn and has no knobs for workers, CORS, or a health check. The exported ASGI app is what you put behind a process manager.

## Run

After `pip install electiondata-my-mcp` (or `uvx`):

```bash
# one worker on 127.0.0.1:8000
electiondata-my-mcp --transport streamable-http

# several workers (same flags the CLI would pass to uvicorn)
electiondata-my-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --workers 4
```

Or hand the app to uvicorn yourself:

```bash
uvicorn electiondata_my_mcp.http_app:app --host 0.0.0.0 --port 8000 --workers 4
```

- MCP endpoint: `http://127.0.0.1:8000/mcp`
- Liveness: `GET /health` → `{"status": "ok"}` (unauthenticated)

`--host`, `--port`, `--workers`, `--allowed-host`, `--allowed-origin`, and `--disable-dns-rebinding-protection` are HTTP-only. Passing them with the default stdio transport is an error, so a desktop config cannot accidentally open a port.

## Client config

Cursor (user `~/.cursor/mcp.json` or project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "electiondata-my": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Claude Code:

```bash
claude mcp add --transport http electiondata-my http://127.0.0.1:8000/mcp
```

## Host and Origin allowlists

Out of the box the app only accepts requests whose `Host` is localhost (`127.0.0.1`, `localhost`, `[::1]`, any port). Behind a real hostname every request is `421 Misdirected Request` until you allowlist what you actually serve.

CLI (repeatable flags; copied into the environment before uvicorn forks workers):

```bash
electiondata-my-mcp --transport streamable-http --host 0.0.0.0 \
  --allowed-host mcp.example.com --allowed-host 'mcp.example.com:*' \
  --allowed-origin https://app.example.com
```

Same values as environment variables, which `uvicorn electiondata_my_mcp.http_app:app` reads directly (see `.env.example`):

```bash
export MCP_ALLOWED_HOSTS='mcp.example.com,mcp.example.com:*'
export MCP_ALLOWED_ORIGINS='https://app.example.com'
export MCP_ENABLE_DNS_REBINDING_PROTECTION=true
uvicorn electiondata_my_mcp.http_app:app --host 0.0.0.0 --workers 4
```

- `MCP_ALLOWED_HOSTS` / `--allowed-host` is the hostname **this server** is served as. List both the bare name and `name:*` if clients include a port.
- `MCP_ALLOWED_ORIGINS` / `--allowed-origin` is the browser origin. It feeds both `CORSMiddleware` and `TransportSecuritySettings.allowed_origins`; they must agree.
- CORS methods and `Mcp-*` headers are fixed (protocol, not deployment). `Mcp-Session-Id` is exposed so a browser client can read the session header.
- Behind a reverse proxy that already controls `Host`, `--disable-dns-rebinding-protection` (or `MCP_ENABLE_DNS_REBINDING_PROTECTION=false`) is the honest setting.

If TLS terminates at a proxy, tell uvicorn to trust `X-Forwarded-*` or redirects from `/mcp` to `/mcp/` will be issued as `http://` and MCP clients will refuse them:

```bash
uvicorn electiondata_my_mcp.http_app:app --proxy-headers --forwarded-allow-ips='<proxy address>'
```

## Workers

`--workers` maps to `uvicorn --workers`. The HTTP app explicitly uses the MCP SDK's
stateless mode, so any worker can serve any request. This server has no elicitation /
`requestState` tools, so you do not need sticky sessions or a shared
`RequestStateSecurity` key.

Each worker process has its own DuckDB database (`connect()` once, lazily on first query) and a bounded cursor pool. Concurrent tool calls check out cursors; when every cursor is busy, a caller waits up to `MCP_DUCKDB_POOL_TIMEOUT` seconds (default 10) and then gets a "retry shortly" tool error. Pool size times `--workers` is the cap on in-flight lake queries for the machine.

```bash
export MCP_DUCKDB_POOL_SIZE=4
export MCP_DUCKDB_POOL_TIMEOUT=10
```

## What stays on stdio

`electiondata-my-mcp` with no flags, `uvx electiondata-my-mcp==…`, and the Claude Desktop / Claude Code / Cursor **command** blocks in the [README](README.md#usage) still start `mcp.run()` over stdio. HTTP is opt-in.
