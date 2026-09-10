from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_duckdb_connection() -> MagicMock:
    connection = MagicMock()

    describe_result = MagicMock()
    describe_result.fetchall.return_value = [
        ("seat", "VARCHAR", None, None, None, None),
        ("majority", "BIGINT", None, None, None, None),
    ]

    query_result = MagicMock()
    query_result.description = [("seat",), ("majority",)]
    query_result.fetchmany.return_value = [
        ("P.001 Wellesley North", 0),
        ("P.029 Kemaman", 0),
    ]
    query_result.columns = ["seat", "majority"]
    query_result.fetchall.return_value = query_result.fetchmany.return_value

    def sql_side_effect(query: str) -> MagicMock:
        if query.startswith("DESCRIBE"):
            return describe_result
        return query_result

    connection.sql.side_effect = sql_side_effect
    connection.execute.return_value.fetchall.return_value = []
    return connection
