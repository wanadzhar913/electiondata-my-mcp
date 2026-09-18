from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
LAKE_READ_TIMEOUT_SECONDS = 120.0


def _structured(result: CallToolResult) -> Any:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    payload = result.structured_content
    # Generic list/dict returns are wrapped as {"result": ...} on the stdio wire.
    if isinstance(payload, dict) and list(payload.keys()) == ["result"]:
        return payload["result"]
    return payload


async def test_stdio_client_queries_headline_stats() -> None:
    # Keep Client enter/exit in this task. A fixture yield is torn down on a
    # different task under pytest-asyncio + anyio, which 3.13 rejects.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "electiondata_my_mcp"],
        cwd=REPO,
    )
    async with Client(params, read_timeout_seconds=LAKE_READ_TIMEOUT_SECONDS) as client:
        tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names >= {
            "list_datasets",
            "describe_dataset",
            "validate_sql",
            "sample_dataset",
            "execute_query",
        }

        listed = _structured(await client.call_tool("list_datasets", {}))
        assert any(dataset["name"] == "headline_stats" for dataset in listed)

        described = _structured(
            await client.call_tool("describe_dataset", {"dataset": "headline_stats"})
        )
        assert described["dataset"] == "headline_stats"
        column_names = [column["name"] for column in described["columns"]]
        assert "seat" in column_names

        sampled = _structured(
            await client.call_tool(
                "sample_dataset",
                {"dataset": "headline_stats", "limit": 1},
            )
        )
        assert sampled["row_count"] == 1
        assert sampled["truncated"] is False
        assert len(sampled["rows"]) == 1
        assert "seat" in sampled["rows"][0]

        queried = _structured(
            await client.call_tool(
                "execute_query",
                {
                    "sql": "SELECT seat FROM headline_stats LIMIT 1",
                    "max_rows": 1,
                },
            )
        )
        assert queried["row_count"] == 1
        assert queried["truncated"] is False
        assert queried["columns"] == ["seat"]
        assert len(queried["rows"]) == 1
        assert queried["rows"][0]["seat"]
