import pytest

from electiondata_my_mcp.dataset_catalog import assert_catalog_covers_datasets, dataset_description
from electiondata_my_mcp.duckdb_lake import DATASETS

pytestmark = pytest.mark.unit


def test_catalog_covers_all_datasets() -> None:
    assert_catalog_covers_datasets()
    assert len(DATASETS) == 27


def test_voter_roll_nsn_se16_notes_missing_saluran() -> None:
    text = dataset_description("voter_roll_nsn_se16")
    assert "2026" in text
    assert "saluran_ballots" in text
    assert "LIMIT" in text
