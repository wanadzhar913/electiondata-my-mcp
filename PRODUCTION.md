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

POSTs to `/mcp` are Streamable HTTP: send `Accept: application/json, text/event-stream`. Replies are SSE (`event: message` then `data: {jsonrpc…}`). There is no `Mcp-Session-Id` (stateless). Hit `127.0.0.1` or `localhost`; another `Host` is `421` until you allowlist it.

```bash
curl -sS http://127.0.0.1:8000/health
# {"status":"ok"}

curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

```text
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-11-25","capabilities":{…},"serverInfo":{"name":"electiondata-my-mcp",…}}}
```

```bash
curl -sS http://127.0.0.1:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"execute_query","arguments":{"sql":"SELECT seat, majority FROM headline_stats ORDER BY majority DESC LIMIT 3","max_rows":3}}}'
```

```text
event: message
data: {"jsonrpc":"2.0","id":2,"result":{"isError":false,"structuredContent":{"columns":["seat","majority"],"rows":[{"seat":"P.106 Damansara","majority":124619},{"seat":"P.104 Subang","majority":115074},{"seat":"P.106 Damansara","majority":106903}],"row_count":3,"truncated":false,"elapsed_ms":105.67}}}
```

`tools/list` and other tools use the same `tools/call` envelope (`list_datasets`, `validate_sql`, `describe_dataset`, `sample_dataset`). Lake queries (`describe_dataset` / `sample_dataset` / `execute_query`) take a moment on first connect.

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

## Docker

The image runs that same app: `uvicorn electiondata_my_mcp.http_app:app` on `0.0.0.0:8000` with four workers. Dev dependencies are not installed (`uv sync --frozen --no-dev`).

```bash
docker build -t electiondata-my-mcp .
docker run --rm -p 8000:8000 electiondata-my-mcp
```

The Dockerfile uses BuildKit cache mounts (`RUN --mount=type=cache`). Docker Buildx does that by default. If `docker build` reports that `--mount` requires BuildKit, prefix the build with `DOCKER_BUILDKIT=1`.

`GET /health` and `POST /mcp` listen on port 8000. A request whose `Host` is `localhost` or `127.0.0.1` (any port) is accepted, which is what `docker run -p 8000:8000` plus `curl http://127.0.0.1:8000/health` sends. Another hostname is `421` until you allowlist it, same as a non-container deploy:

```bash
docker run --rm -p 8000:8000 \
  -e MCP_ALLOWED_HOSTS='mcp.example.com,mcp.example.com:*' \
  -e MCP_ALLOWED_ORIGINS='https://app.example.com' \
  electiondata-my-mcp
```

`APP_VERSION` is the image build arg `VERSION` (default `0.2.0`). The server does not read it; it only labels the image (`docker build --build-arg VERSION=0.2.0`).

Pool size is still per worker. The image's four workers and the default `MCP_DUCKDB_POOL_SIZE=4` cap the machine at about sixteen in-flight lake queries. Override the pool the same way as the allowlists (`-e MCP_DUCKDB_POOL_SIZE=4`).

## What stays on stdio

`electiondata-my-mcp` with no flags, `uvx electiondata-my-mcp==…`, and the Claude Desktop / Claude Code / Cursor **command** blocks in the [README](README.md#usage) still start `mcp.run()` over stdio. HTTP is opt-in.

## Design

The [Workers](#workers) section is the runbook. This is why it looks like that.

### Export an ASGI app instead of `mcp.run("streamable-http")`

`mcp.run("streamable-http")` starts one in-process uvicorn. It has no place to put a worker count, CORS, or `GET /health`. Production needs all three, so `http_app.py` builds a host Starlette around `mcp.streamable_http_app()` and the CLI (or a container) hands that app to uvicorn.

Stdio stays the default. `--host`, `--port`, `--workers`, and the allowlist flags are rejected unless `--transport streamable-http`, so a desktop config cannot open a port by accident.

### Stateless HTTP, one process per worker

The HTTP transport sets `stateless_http=True`. A request does not depend on a process-local MCP session, so any worker can serve any request. There is no `Mcp-Session-Id` to pin, and no sticky-session requirement at the load balancer.

This server has no elicitation and no `requestState` tools, so it also does not need a shared `RequestStateSecurity` key. Session affinity would only matter if a later request had to land on the worker that sealed the previous one.

```mermaid
flowchart TB
    Clients[Concurrent MCP clients]

    subgraph Worker1[Uvicorn worker 1]
        Pool1[DuckDB cursor pool]
        DB1[One DuckDB database]
        Pool1 --> DB1
    end

    subgraph Worker2[Uvicorn worker 2]
        Pool2[DuckDB cursor pool]
        DB2[One DuckDB database]
        Pool2 --> DB2
    end

    Clients -->|Stateless request| Worker1
    Clients -->|Stateless request| Worker2

    DB1 --> Lake[ElectionData.MY Parquet lake]
    DB2 --> Lake
```

CORS and DNS-rebinding protection sit on that same app. Methods and `Mcp-*` headers are the protocol, not a deployment choice, so they are fixed in code. `Host` and `Origin` allowlists are the deployment choice: localhost by default, because a public hostname that is not allowlisted is `421`. Behind a proxy that already checks `Host`, turning DNS-rebinding protection off is the honest setting. If TLS ends at that proxy, uvicorn has to trust `X-Forwarded-*` or it will redirect `/mcp` to `http://`.

`GET /health` is unauthenticated on purpose. A probe should not need a session or a token to learn that the process is up.

### One DuckDB database per process, cursors for concurrency

Each worker is its own process. The first query in that process lazily opens one root DuckDB connection, installs `httpfs`, and registers the lake views. Later queries do not open another database. They check out a `cursor()` from that root connection. DuckDB connections are not safe to share across threads; cursors over the same database are.

```mermaid
sequenceDiagram
    participant A as Query A
    participant B as Query B
    participant P as Cursor pool
    participant D as Root DuckDB connection
    participant L as Data lake

    A->>P: Acquire cursor
    P->>D: Lazily connect and create cursor 1
    B->>P: Acquire cursor
    P->>D: Create cursor 2

    par Concurrent execution
        A->>L: Read required Parquet ranges
        B->>L: Read required Parquet ranges
    end

    A->>P: Return cursor 1
    B->>P: Return cursor 2
```

Cursor creation takes a lock so two threads cannot both decide they are the one to `connect()`. Idle cursors sit in a `LifoQueue`. Checkout returns the cursor in a `finally` block, including when the query raises, so a failed query does not shrink the pool.

`LifoQueue` is deliberate. Handing back the most recently used cursor keeps a small set warm — the same httpfs connections and metadata cache — instead of round-robining across every cursor and cold-starting each one. `queue.get` does not promise fairness among waiters. Under sustained saturation a given request can lose the race; that is a reason for a short timeout and a clear busy error, not a long hopeful wait.

### Backpressure

The pool size is fixed (`MCP_DUCKDB_POOL_SIZE`, default 4). When every cursor is busy, the next query waits instead of opening unbounded DuckDB work. After `MCP_DUCKDB_POOL_TIMEOUT` seconds (default 10) it gets `PoolTimeoutError`, which the tool layer turns into "Too many concurrent lake queries; retry shortly."

```mermaid
flowchart LR
    Request[Incoming query] --> Available{Cursor available?}
    Available -->|Yes| Execute[Execute query]
    Available -->|No| Wait[Wait for checkout timeout]
    Wait --> Freed{Cursor returned?}
    Freed -->|Yes| Execute
    Freed -->|No| Busy[Return retry shortly tool error]
    Execute --> Return[Return cursor to pool]
```

| Setting | Meaning | Default |
| --- | --- | --- |
| `MCP_DUCKDB_POOL_SIZE` | Cursors per worker | 4 |
| `MCP_DUCKDB_POOL_TIMEOUT` | Max checkout wait | 10 seconds |
| `--workers` | uvicorn worker processes | 1 |

The cap on simultaneous lake queries for the machine is about `workers × MCP_DUCKDB_POOL_SIZE`. Four workers and a pool of four is sixteen. Each worker still owns one DuckDB database and still registers the lake views once.
