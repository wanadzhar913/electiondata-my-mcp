#!/usr/bin/env python3
"""ElectionData.MY MCP server."""
# Annotations stay eager: `mcp dev` execs this file without registering it in
# sys.modules, so pydantic cannot resolve stringified hints on decorated functions.

import argparse
import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import duckdb
from mcp.server import ServerRequestContext
from mcp.server.context import CallNext, HandlerResult
from mcp.server.mcpserver import MCPServer, UserMessage
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from electiondata_my_mcp.duckdb_lake import DATASETS, LAZY
from electiondata_my_mcp.duckdb_pool import DuckDBPool, PoolTimeoutError, pool_from_env
from electiondata_my_mcp.prompt_loader import load_prompt
from electiondata_my_mcp.query_validator import validate_query
from electiondata_my_mcp.settings import apply_cli_overrides

DEFAULT_MAX_ROWS = 100
ABSOLUTE_MAX_ROWS = 1_000
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000
DEFAULT_HTTP_WORKERS = 1
ASGI_APP_IMPORT_STRING = "electiondata_my_mcp.http_app:app"

logger = logging.getLogger(__name__)

_pool: DuckDBPool = pool_from_env()

DATASET_DESCRIPTIONS: dict[str, str] = {
    "headline_ballots": "Candidate-level results for every Parliament and DUN contest.",
    "headline_stats": "Seat-level statistics for every Parliament and DUN contest.",
    "voter_demographics": "Seat-level voter demographics with nationwide ethnic groupings.",
    "voter_demographics_sarawak": "Sarawak seat demographics with Sarawak-specific ethnic groups.",
    "voter_demographics_sabah": "Sabah seat demographics with Sabah-specific ethnic groups.",
}


async def log_timing(
    ctx: ServerRequestContext[Any, Any],
    call_next: CallNext,
) -> HandlerResult:
    start = time.perf_counter()
    try:
        return await call_next(ctx)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("%s took %.1f ms", ctx.method, elapsed_ms)


mcp = MCPServer(
    "electiondata-my-mcp",
    instructions=(
        "Query Malaysian election data from the ElectionData.MY public data lake "
        "using DuckDB SQL. Read electiondata://query-guide before writing SQL."
    ),
    middleware=[log_timing],
)


@contextmanager
def _connection() -> Iterator[duckdb.DuckDBPyConnection]:
    try:
        with _pool.acquire() as con:
            yield con
    except PoolTimeoutError as error:
        raise ToolError("Too many concurrent lake queries; retry shortly.") from error


def _dataset_description(name: str) -> str:
    if name in DATASET_DESCRIPTIONS:
        return DATASET_DESCRIPTIONS[name]
    if name.startswith("saluran_ballots_"):
        return f"Saluran-level candidate ballots for {name.removeprefix('saluran_ballots_')}."
    if name.startswith("saluran_stats_"):
        return f"Saluran-level statistics for {name.removeprefix('saluran_stats_')}."
    if name.startswith("voter_roll_"):
        return f"Voter roll for {name.removeprefix('voter_roll_')}."
    return "ElectionData.MY lake dataset."


def _require_valid_query(sql: str) -> list[str]:
    result = validate_query(sql)
    if not result.valid:
        raise ToolError("; ".join(result.errors))
    return result.tables


def _serialize_rows(
    result: duckdb.DuckDBPyRelation,
    max_rows: int,
) -> tuple[list[str], list[dict[str, Any]], bool]:
    columns = [column[0] for column in result.description]
    fetched = result.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows = fetched[:max_rows]
    serialized = [dict(zip(columns, row, strict=True)) for row in rows]
    return columns, serialized, truncated


@mcp.resource(
    "electiondata://query-guide",
    name="query-guide",
    description="ElectionData.MY Query Builder schema and SQL rules.",
    mime_type="text/markdown",
)
def query_guide() -> str:
    return load_prompt()


@mcp.prompt(
    name="build_election_query",
    description="Prepare the client to write DuckDB SQL for an election question.",
)
def build_election_query(question: str) -> list[UserMessage]:
    guide = load_prompt()
    return [
        UserMessage(guide),
        UserMessage(
            "Using the schema and rules above, write a single DuckDB SQL query for "
            f"this question:\n\n{question}"
        ),
    ]


@mcp.tool()
def list_datasets() -> list[dict[str, Any]]:
    """List available lake datasets and whether each is streamed over HTTP."""
    return [
        {
            "name": name,
            "url": url,
            "description": _dataset_description(name),
            "streamed": name in LAZY,
        }
        for name, url in DATASETS.items()
    ]


@mcp.tool()
def describe_dataset(dataset: str) -> dict[str, Any]:
    """Return column names and types for a lake dataset."""
    if dataset not in DATASETS:
        raise ToolError(f"Unknown dataset: {dataset}")

    with _connection() as con:
        try:
            rows = con.sql(f"DESCRIBE SELECT * FROM {dataset}").fetchall()
        except duckdb.Error as error:
            raise ToolError(f"Unable to describe dataset {dataset}: {error}") from error

    return {
        "dataset": dataset,
        "description": _dataset_description(dataset),
        "streamed": dataset in LAZY,
        "columns": [{"name": name, "type": column_type} for name, column_type, *_ in rows],
    }


@mcp.tool()
def validate_sql(sql: str) -> dict[str, Any]:
    """Validate read-only SQL against lake table and voter-roll rules."""
    result = validate_query(sql)
    return {
        "valid": result.valid,
        "tables": result.tables,
        "errors": result.errors,
        "warnings": result.warnings,
    }


@mcp.tool()
def sample_dataset(dataset: str, limit: int = 5) -> dict[str, Any]:
    """Return a small sample of rows from a dataset."""
    if dataset not in DATASETS:
        raise ToolError(f"Unknown dataset: {dataset}")
    if limit < 1 or limit > 100:
        raise ToolError("limit must be between 1 and 100")

    sql = f"SELECT * FROM {dataset} LIMIT {limit}"
    _require_valid_query(sql)
    return execute_query(sql, max_rows=limit)


@mcp.tool()
def execute_query(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> dict[str, Any]:
    """Execute validated read-only SQL against the ElectionData.MY lake."""
    if max_rows < 1 or max_rows > ABSOLUTE_MAX_ROWS:
        raise ToolError(f"max_rows must be between 1 and {ABSOLUTE_MAX_ROWS}")

    _require_valid_query(sql)
    started = time.perf_counter()

    with _connection() as con:
        try:
            result = con.sql(sql)
            if result is None:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                    "elapsed_ms": elapsed_ms,
                }

            columns, rows, truncated = _serialize_rows(result, max_rows)
        except duckdb.Error as error:
            raise ToolError(f"Query failed: {error}") from error

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ElectionData.MY MCP server (stdio by default; Streamable HTTP optional).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help=f"HTTP bind host (default: {DEFAULT_HTTP_HOST}; streamable-http only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"HTTP bind port (default: {DEFAULT_HTTP_PORT}; streamable-http only)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"uvicorn worker processes (default: {DEFAULT_HTTP_WORKERS}; streamable-http only)",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=None,
        metavar="HOST",
        help="DNS-rebinding Host allowlist entry; repeatable (streamable-http only)",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        dest="allowed_origins",
        default=None,
        metavar="ORIGIN",
        help="CORS and Origin allowlist entry; repeatable (streamable-http only)",
    )
    parser.add_argument(
        "--disable-dns-rebinding-protection",
        action="store_true",
        help="Turn off Host/Origin checks (streamable-http only; for a trusted reverse proxy)",
    )
    args = parser.parse_args(argv)

    http_only_set = any(
        [
            args.host is not None,
            args.port is not None,
            args.workers is not None,
            args.allowed_hosts,
            args.allowed_origins,
            args.disable_dns_rebinding_protection,
        ]
    )
    if args.transport == "stdio" and http_only_set:
        parser.error(
            "--host, --port, --workers, --allowed-host, --allowed-origin, and "
            "--disable-dns-rebinding-protection require --transport streamable-http"
        )

    if args.transport == "streamable-http":
        if args.host is None:
            args.host = DEFAULT_HTTP_HOST
        if args.port is None:
            args.port = DEFAULT_HTTP_PORT
        if args.workers is None:
            args.workers = DEFAULT_HTTP_WORKERS
        if args.port < 1 or args.port > 65535:
            parser.error("--port must be between 1 and 65535")
        if args.workers < 1:
            parser.error("--workers must be at least 1")

    return args


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.transport == "stdio":
        mcp.run()
        return

    apply_cli_overrides(
        allowed_hosts=args.allowed_hosts,
        allowed_origins=args.allowed_origins,
        enable_dns_rebinding_protection=(False if args.disable_dns_rebinding_protection else None),
    )
    import uvicorn

    uvicorn.run(
        ASGI_APP_IMPORT_STRING,
        host=args.host,
        port=args.port,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
