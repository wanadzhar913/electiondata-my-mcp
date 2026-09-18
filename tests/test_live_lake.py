from __future__ import annotations

import pytest

from electiondata_my_mcp.duckdb_lake import connect

pytestmark = pytest.mark.integration


def test_headline_stats_is_reachable() -> None:
    connection = connect()
    try:
        rows = connection.sql("SELECT seat FROM headline_stats LIMIT 1").fetchall()
    finally:
        connection.close()

    assert len(rows) == 1
    assert isinstance(rows[0][0], str)
    assert rows[0][0]
