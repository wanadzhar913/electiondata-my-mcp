from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx2
import pytest

from electiondata_my_mcp import oauth
from electiondata_my_mcp.server import mcp
from fake_authorization_server import FakeAuthorizationServer


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if "unit" not in item.keywords and "integration" not in item.keywords:
            raise pytest.UsageError(
                f"{item.nodeid} is missing @pytest.mark.unit or @pytest.mark.integration"
            )


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


@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Start with no OAuth variables; afterwards, leave the shared ``mcp`` open again."""
    for name in oauth._ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield monkeypatch
    for name in oauth._ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    oauth.apply_oauth_config(mcp)


@pytest.fixture
def auth_server(oauth_env: pytest.MonkeyPatch) -> FakeAuthorizationServer:
    """A fake authorization server that the resource server's JWKS and introspection calls reach."""
    server = FakeAuthorizationServer()
    oauth_env.setattr(
        oauth,
        "_http_client",
        lambda: httpx2.AsyncClient(transport=httpx2.ASGITransport(app=server.app)),
    )
    return server
