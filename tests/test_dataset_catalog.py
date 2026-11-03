import pytest

from electiondata_my_mcp.dataset_catalog import (
    LOOKUP_DATASETS,
    assert_catalog_covers_datasets,
    dataset_description,
    dataset_use_for,
)
from electiondata_my_mcp.duckdb_lake import DATASETS, LAZY

pytestmark = pytest.mark.unit

LOOKUP_NAMES = (
    "lookup_candidate",
    "lookup_coalition",
    "lookup_coalition_succession",
    "lookup_dates",
    "lookup_party",
    "lookup_party_succession",
    "lookup_prk",
)


def test_catalog_covers_all_datasets() -> None:
    assert_catalog_covers_datasets()
    assert len(DATASETS) == 34


@pytest.mark.parametrize("name", LOOKUP_NAMES)
def test_lookup_tables_are_non_lazy_mcp_extensions(name: str) -> None:
    assert name in LOOKUP_DATASETS
    assert name not in LAZY
    assert DATASETS[name] == (f"https://lake.electiondata.my/results_headline/{name}.parquet")
    description = dataset_description(name)
    assert "MCP-specific" in description
    assert "55c6098" in description
    assert description != "ElectionData.MY lake dataset."
    assert dataset_use_for(name) != "See get_query_guide for schema and join rules."


def test_voter_roll_nsn_se16_notes_missing_saluran() -> None:
    text = dataset_description("voter_roll_nsn_se16")
    assert "2026" in text
    assert "saluran_ballots" in text
    assert "LIMIT" in text
