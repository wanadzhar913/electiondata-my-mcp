# Auth0 and this MCP server

The ElectionData.MY MCP server is an OAuth 2.1 **resource server** for Streamable HTTP. It verifies JWT access tokens against your identity provider’s JWKS; it does not sign users in or issue tokens. [Auth0](https://auth0.com/) is one supported authorization server.

General deploy and env var reference: [PRODUCTION.md](../PRODUCTION.md).

## What you configure in Auth0

1. **API (Resource Server)**
   - **Identifier** must match `MCP_OAUTH_RESOURCE_URL` (for local integration tests and CI this repo uses `http://127.0.0.1:8000/mcp`).
   - **Signing algorithm**: RS256.
   - **Permission (scope)**: `electiondata:read`.

2. **Machine-to-machine application** (for headless clients: CI, scripts, backend services)
   - Authorize it for the API above and grant `electiondata:read`.
   - Copy **Client ID** and **Client Secret** from this app—not from a SPA or regular web application.

3. **Resource Parameter Compatibility Profile** (Auth0 dashboard)
   Enable this if MCP clients send RFC 8707 `resource`; see [PRODUCTION.md](../PRODUCTION.md).

Interactive user login (browser, PKCE) uses a different Auth0 application type; the live test in this repo uses **client credentials** only.

## MCP server environment (`MCP_OAUTH_*`)

Copy from [.env.example](../.env.example) and set on the process that runs Streamable HTTP:

| Variable | Auth0 value |
| --- | --- |
| `MCP_OAUTH_ISSUER_URL` | `https://<tenant>/` — trailing slash must match the token `iss` claim |
| `MCP_OAUTH_RESOURCE_URL` | Same as the Auth0 API identifier (e.g. `http://127.0.0.1:8000/mcp` for tests) |
| `MCP_OAUTH_JWKS_URL` | `https://<tenant>/.well-known/jwks.json` |
| `MCP_OAUTH_REQUIRED_SCOPES` | `electiondata:read` (default if unset) |

Stdio transport and `GET /health` ignore these variables.

## Get an access token (client credentials)

```bash
curl -s "https://<tenant>/oauth/token" \
  -H 'content-type: application/json' \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "<M2M_CLIENT_ID>",
    "client_secret": "<M2M_CLIENT_SECRET>",
    "audience": "http://127.0.0.1:8000/mcp"
  }'
```

Call `/mcp` with `Authorization: Bearer <access_token>`.

## Live test in this repository

`tests/test_live_auth0_oauth.py` is marked `integration`. It:

- Fetches a real token from Auth0 when these are set: `AUTH0_DOMAIN`, `AUTH0_CI_CLIENT_ID`, `AUTH0_CI_CLIENT_SECRET` (tenant hostname only, no `https://`).
- Configures the in-process MCP app with the matching `MCP_OAUTH_*` values.
- Asserts a tool call succeeds with a valid token and returns **401** without a bearer token.

```bash
export AUTH0_DOMAIN=your-tenant.us.auth0.com
export AUTH0_CI_CLIENT_ID=...
export AUTH0_CI_CLIENT_SECRET=...
uv run pytest tests/test_live_auth0_oauth.py -m integration --no-cov
```

GitHub Actions integration job uses the same three names as repository secrets.

## Example: provision Auth0 via the Auth0 MCP server

[`scripts/auth0_setup.py`](../scripts/auth0_setup.py) is an **optional** example script. It signs in with the same device flow as `@auth0/auth0-mcp-server init`, then uses the Auth0 MCP server to idempotently create the API, M2M app, and grant described above. Run it when you prefer automation over clicking through the dashboard:

```bash
uv sync --group dev
uv run python scripts/auth0_setup.py
```

You need Node.js for `npx @auth0/auth0-mcp-server`. On headless hosts without a system keyring, the script passes a short-lived Management API token via `AUTH0_TOKEN` instead of persisting a session—same pattern as documented for WSL in the script docstring.

After the script finishes, set MCP runtime vars from the table above and store `AUTH0_CI_*` secrets for CI from the M2M application.
