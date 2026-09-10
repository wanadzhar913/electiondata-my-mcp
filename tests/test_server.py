from __future__ import annotations

from unittest.mock import MagicMock, patch

import duckdb
import pytest
from inline_snapshot import snapshot
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from electiondata_my_mcp import server
from electiondata_my_mcp.server import (
    ABSOLUTE_MAX_ROWS,
    build_election_query,
    describe_dataset,
    execute_query,
    list_datasets,
    mcp,
    query_guide,
    sample_dataset,
    validate_sql,
)


@pytest.fixture
def anyio_backend():  
    return "asyncio"


@pytest.fixture
async def client():  
    async with Client(mcp, raise_exceptions=True) as c:
        yield c


@pytest.fixture(autouse=True)
def reset_connection() -> None:
    server._connection = None
    yield
    server._connection = None


@pytest.fixture
def patched_connection(mock_duckdb_connection: MagicMock) -> MagicMock:
    server._connection = mock_duckdb_connection
    return mock_duckdb_connection


def test_list_datasets_includes_known_table() -> None:
    datasets = list_datasets()
    assert len(datasets) == 27
    headline_stats = next(item for item in datasets if item["name"] == "headline_stats")
    assert headline_stats["streamed"] is False
    assert "Seat-level statistics" in headline_stats["description"]


def test_list_datasets_describes_saluran_tables() -> None:
    datasets = list_datasets()
    saluran = next(item for item in datasets if item["name"] == "saluran_ballots_ge15")
    assert saluran["description"] == "Saluran-level candidate ballots for ge15."


def test_describe_dataset_returns_columns(patched_connection: MagicMock) -> None:
    result = describe_dataset("headline_stats")
    assert result["dataset"] == "headline_stats"
    assert result["columns"] == [
        {"name": "seat", "type": "VARCHAR"},
        {"name": "majority", "type": "BIGINT"},
    ]
    patched_connection.sql.assert_called_once()


def test_describe_dataset_rejects_unknown_dataset() -> None:
    with pytest.raises(ToolError, match="Unknown dataset"):
        describe_dataset("not_a_dataset")


def test_describe_dataset_wraps_duckdb_errors(patched_connection: MagicMock) -> None:
    patched_connection.sql.side_effect = duckdb.Error("boom")
    with pytest.raises(ToolError, match="Unable to describe dataset"):
        describe_dataset("headline_stats")


def test_validate_sql_returns_validation_details() -> None:
    result = validate_sql("SELECT seat FROM headline_stats LIMIT 1")
    assert result["valid"] is True
    assert result["tables"] == ["headline_stats"]
    assert result["errors"] == []


def test_sample_dataset_executes_limited_query(patched_connection: MagicMock) -> None:
    result = sample_dataset("headline_stats", limit=2)
    assert result["row_count"] == 2
    assert result["columns"] == ["seat", "majority"]


def test_sample_dataset_rejects_unknown_dataset() -> None:
    with pytest.raises(ToolError, match="Unknown dataset"):
        sample_dataset("missing_dataset")


def test_sample_dataset_rejects_invalid_limit() -> None:
    with pytest.raises(ToolError, match="limit must be between"):
        sample_dataset("headline_stats", limit=0)


def test_execute_query_returns_rows(patched_connection: MagicMock) -> None:
    result = execute_query(
        "SELECT seat, majority FROM headline_stats ORDER BY majority LIMIT 2",
        max_rows=2,
    )
    assert result["row_count"] == 2
    assert result["truncated"] is False
    assert "elapsed_ms" in result


def test_execute_query_marks_truncated_results(patched_connection: MagicMock) -> None:
    query_result = MagicMock()
    query_result.description = [("seat",), ("majority",)]
    query_result.fetchmany.return_value = [
        ("a", 1),
        ("b", 2),
        ("c", 3),
    ]
    patched_connection.sql.side_effect = None
    patched_connection.sql.return_value = query_result

    result = execute_query("SELECT seat, majority FROM headline_stats LIMIT 3", max_rows=2)
    assert result["row_count"] == 2
    assert result["truncated"] is True


def test_execute_query_handles_none_result(patched_connection: MagicMock) -> None:
    patched_connection.sql.side_effect = None
    patched_connection.sql.return_value = None

    result = execute_query("SELECT seat FROM headline_stats LIMIT 1")
    assert result == {
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "elapsed_ms": pytest.approx(0, abs=50),
    }


def test_execute_query_rejects_invalid_sql() -> None:
    with pytest.raises(ToolError, match="Only SELECT queries are allowed"):
        execute_query("DELETE FROM headline_stats")


def test_execute_query_rejects_invalid_max_rows() -> None:
    with pytest.raises(ToolError, match="max_rows must be between"):
        execute_query("SELECT seat FROM headline_stats LIMIT 1", max_rows=ABSOLUTE_MAX_ROWS + 1)


def test_execute_query_wraps_duckdb_errors(patched_connection: MagicMock) -> None:
    patched_connection.sql.side_effect = duckdb.Error("syntax error")
    with pytest.raises(ToolError, match="Query failed"):
        execute_query("SELECT seat FROM headline_stats LIMIT 1")


@patch("electiondata_my_mcp.server.load_prompt", return_value="query guide")
def test_query_guide_returns_prompt(mock_load_prompt: MagicMock) -> None:
    assert query_guide() == "query guide"
    mock_load_prompt.assert_called_once()


@patch("electiondata_my_mcp.server.load_prompt", return_value="query guide")
def test_build_election_query_returns_messages(mock_load_prompt: MagicMock) -> None:
    messages = build_election_query("Who won GE-15?")
    assert len(messages) == 2
    assert messages[0].content.text == "query guide"
    assert "Who won GE-15?" in messages[1].content.text


@patch("electiondata_my_mcp.server.connect")
def test_get_connection_is_lazy(mock_connect: MagicMock, mock_duckdb_connection: MagicMock) -> None:
    mock_connect.return_value = mock_duckdb_connection
    first = server._get_connection()
    second = server._get_connection()
    assert first is second
    mock_connect.assert_called_once()


@pytest.mark.anyio
async def test_call_validate_sql_tool(client: Client):
    result = await client.call_tool(
        "validate_sql",
        {"sql": "SELECT seat FROM headline_stats LIMIT 1"},
    )
    # Drop the server identity stamp in `_meta`; it is not what this test is about.
    result.meta = None
    assert result == snapshot(
        CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        '{\n  "valid": true,\n  "tables": [\n'
                        '    "headline_stats"\n  ],\n  "errors": [],\n  "warnings": []\n}'
                    ),
                )
            ],
            structured_content={
                "valid": True,
                "tables": ["headline_stats"],
                "errors": [],
                "warnings": [],
            },
        )
    )
