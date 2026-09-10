from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from electiondata_my_mcp.duckdb_lake import DATASETS, LAZY, connect, main


@patch("duckdb.connect")
def test_connect_registers_httpfs_and_views(mock_connect: MagicMock) -> None:
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = []
    mock_connect.return_value = connection

    result = connect()

    assert result is connection
    install_calls = [call.args[0] for call in connection.execute.call_args_list]
    assert install_calls[0] == "INSTALL httpfs; LOAD httpfs;"
    assert sum(1 for sql in install_calls if sql.startswith("CREATE OR REPLACE VIEW")) == len(
        DATASETS
    )


@patch("duckdb.connect")
def test_connect_with_cache_materialises_tables(mock_connect: MagicMock) -> None:
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = []
    mock_connect.return_value = connection

    connect(db_path="lake.duckdb", cache=True)

    create_table_calls = [
        call.args[0]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("CREATE TABLE")
    ]
    assert len(create_table_calls) == len(DATASETS) - len(LAZY)


@patch("duckdb.connect")
def test_connect_with_cache_skips_existing_tables(mock_connect: MagicMock) -> None:
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [("headline_stats",)]
    mock_connect.return_value = connection

    connect(db_path="lake.duckdb", cache=True)

    create_table_calls = [
        call.args[0]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("CREATE TABLE")
    ]
    assert "headline_stats" not in " ".join(create_table_calls)
    assert create_table_calls


def test_main_lists_tables(capsys) -> None:
    with patch.object(sys, "argv", ["duckdb_lake.py", "--tables"]):
        assert main() == 0

    output = capsys.readouterr().out
    assert "headline_stats" in output
    assert "voter_roll_ge15 (streamed, large)" in output


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_outputs_table_format(mock_connect: MagicMock, capsys) -> None:
    relation = MagicMock()
    relation.show = MagicMock()
    connection = MagicMock()
    connection.sql.return_value = relation
    mock_connect.return_value = connection

    with patch.object(
        sys,
        "argv",
        ["duckdb_lake.py", "SELECT seat FROM headline_stats LIMIT 1"],
    ):
        assert main() == 0

    relation.show.assert_called_once_with(max_rows=100)


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_runs_sql_from_argument(mock_connect: MagicMock, capsys) -> None:
    relation = MagicMock()
    relation.columns = ["seat"]
    relation.fetchall.return_value = [("P.001",)]
    connection = MagicMock()
    connection.sql.return_value = relation
    mock_connect.return_value = connection

    with patch.object(
        sys,
        "argv",
        ["duckdb_lake.py", "SELECT seat FROM headline_stats LIMIT 1"],
    ):
        assert main() == 0

    connection.sql.assert_called_once_with("SELECT seat FROM headline_stats LIMIT 1")


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_reads_sql_from_file(mock_connect: MagicMock, tmp_path) -> None:
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT 1", encoding="utf-8")
    connection = MagicMock()
    connection.sql.return_value = None
    mock_connect.return_value = connection

    with patch.object(sys, "argv", ["duckdb_lake.py", "-f", str(sql_file)]):
        assert main() == 0

    connection.sql.assert_called_once_with("SELECT 1")


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_outputs_json(mock_connect: MagicMock, capsys) -> None:
    relation = MagicMock()
    relation.columns = ["seat", "majority"]
    relation.fetchall.return_value = [("P.001", 10)]
    connection = MagicMock()
    connection.sql.return_value = relation
    mock_connect.return_value = connection

    with patch.object(
        sys,
        "argv",
        ["duckdb_lake.py", "--format", "json", "SELECT seat FROM headline_stats LIMIT 1"],
    ):
        assert main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"seat": "P.001", "majority": 10}]


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_outputs_csv(mock_connect: MagicMock, capsys) -> None:
    relation = MagicMock()
    relation.columns = ["seat"]
    relation.fetchall.return_value = [("P.001",)]
    connection = MagicMock()
    connection.sql.return_value = relation
    mock_connect.return_value = connection

    with patch.object(
        sys,
        "argv",
        ["duckdb_lake.py", "--format", "csv", "SELECT seat FROM headline_stats LIMIT 1"],
    ):
        assert main() == 0

    output = capsys.readouterr().out.strip().splitlines()
    assert output == ["seat", "P.001"]


@patch("electiondata_my_mcp.duckdb_lake.connect")
def test_main_reads_sql_from_stdin(mock_connect: MagicMock) -> None:
    connection = MagicMock()
    connection.sql.return_value = None
    mock_connect.return_value = connection

    with patch.object(sys, "argv", ["duckdb_lake.py"]):
        with patch.object(sys.stdin, "isatty", return_value=False):
            with patch.object(sys.stdin, "read", return_value="SELECT 1"):
                assert main() == 0

    connection.sql.assert_called_once_with("SELECT 1")


def test_main_errors_without_sql(tmp_path) -> None:
    with patch.object(sys, "argv", ["duckdb_lake.py"]):
        with patch.object(sys.stdin, "isatty", return_value=True):
            with pytest.raises(SystemExit):
                main()


def test_main_errors_on_empty_sql(tmp_path) -> None:
    sql_file = tmp_path / "empty.sql"
    sql_file.write_text("   ", encoding="utf-8")

    with patch.object(sys, "argv", ["duckdb_lake.py", "-f", str(sql_file)]):
        with pytest.raises(SystemExit):
            main()
