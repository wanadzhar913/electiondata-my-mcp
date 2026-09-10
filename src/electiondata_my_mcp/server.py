#!/usr/bin/env python3
"""ElectionData.MY MCP server."""
# Annotations stay eager: `mcp dev` execs this file without registering it in
# sys.modules, so pydantic cannot resolve stringified hints on decorated functions.

import time
from typing import Any

import duckdb
from mcp.server.mcpserver import MCPServer, UserMessage
from mcp.server.mcpserver.exceptions import ToolError

from electiondata_my_mcp.duckdb_lake import DATASETS, LAZY, connect
from electiondata_my_mcp.prompt_loader import load_prompt
from electiondata_my_mcp.query_validator import validate_query

DEFAULT_MAX_ROWS = 100
ABSOLUTE_MAX_ROWS = 1_000

mcp = MCPServer(
    "electiondata-my-mcp",
    instructions=(
        "Query Malaysian election data from the ElectionData.MY public data lake "
        "using DuckDB SQL. Read electiondata://query-guide before writing SQL."
    ),
)

_connection: duckdb.DuckDBPyConnection | None = None

DATASET_DESCRIPTIONS: dict[str, str] = {
    "headline_ballots": "Candidate-level results for every Parliament and DUN contest.",
    "headline_stats": "Seat-level statistics for every Parliament and DUN contest.",
    "voter_demographics": "Seat-level voter demographics with nationwide ethnic groupings.",
    "voter_demographics_sarawak": "Sarawak seat demographics with Sarawak-specific ethnic groups.",
    "voter_demographics_sabah": "Sabah seat demographics with Sabah-specific ethnic groups.",
}


def _get_connection() -> duckdb.DuckDBPyConnection:
    global _connection
    if _connection is None:
        _connection = connect()
    return _connection


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

    con = _get_connection()
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
    con = _get_connection()
    started = time.perf_counter()

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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
