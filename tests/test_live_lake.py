from __future__ import annotations

import pytest

from electiondata_my_mcp.duckdb_lake import DATASETS, connect

LOOKUP_COLUMNS = {
    "lookup_candidate": [
        "candidate_uid",
        "candidate_rn",
        "name",
        "sex",
        "ethnicity",
        "dob",
    ],
    "lookup_coalition": [
        "coalition_uid",
        "coalition",
        "coalition_name_en",
        "coalition_name_bm",
        "year_start",
        "year_end",
        "notes",
    ],
    "lookup_coalition_succession": [
        "predecessor_uid",
        "successor_uid",
        "type",
        "year",
        "notes",
    ],
    "lookup_dates": ["state", "election_number", "date"],
    "lookup_party": [
        "party_uid",
        "party",
        "party_name_en",
        "party_name_bm",
        "year_formed",
        "year_end",
        "notes",
    ],
    "lookup_party_succession": [
        "predecessor_uid",
        "successor_uid",
        "type",
        "year",
        "notes",
    ],
    "lookup_prk": ["date", "state", "seat", "trigger", "notes"],
}

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


def test_lookup_tables_match_published_schemas() -> None:
    connection = connect()
    try:
        for name, expected_columns in LOOKUP_COLUMNS.items():
            described = connection.sql(f"DESCRIBE SELECT * FROM {name}").fetchall()
            columns = [row[0] for row in described]
            assert columns == expected_columns
            count = connection.sql(f"SELECT count(*) FROM {name}").fetchone()
            assert count is not None
            assert count[0] > 0
            assert DATASETS[name].endswith(f"/{name}.parquet")
    finally:
        connection.close()
