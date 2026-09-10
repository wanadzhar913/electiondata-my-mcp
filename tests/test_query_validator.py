from electiondata_my_mcp.query_validator import referenced_tables, validate_query


def test_accepts_simple_select() -> None:
    result = validate_query("SELECT seat, majority FROM headline_stats ORDER BY majority LIMIT 5")
    assert result.valid
    assert result.tables == ["headline_stats"]


def test_accepts_with_clause() -> None:
    sql = """
    WITH winners AS (
      SELECT seat FROM headline_ballots WHERE result = 'won'
    )
    SELECT seat FROM winners
    JOIN headline_stats USING (seat)
    LIMIT 10
    """
    result = validate_query(sql)
    assert result.valid
    assert "headline_ballots" in result.tables
    assert "headline_stats" in result.tables


def test_rejects_empty_sql() -> None:
    result = validate_query("   ")
    assert not result.valid
    assert result.errors == ["SQL must not be empty"]


def test_rejects_mutating_sql() -> None:
    result = validate_query("DELETE FROM headline_stats")
    assert not result.valid
    assert any("SELECT" in error for error in result.errors)


def test_rejects_multiple_statements() -> None:
    result = validate_query("SELECT 1; SELECT 2")
    assert not result.valid
    assert any("single SQL statement" in error for error in result.errors)


def test_rejects_forbidden_keyword() -> None:
    result = validate_query("INSERT INTO headline_stats SELECT 1")
    assert not result.valid
    assert any("Forbidden keyword: INSERT" in error for error in result.errors)


def test_rejects_forbidden_function() -> None:
    result = validate_query("SELECT * FROM read_parquet('/tmp/evil.parquet')")
    assert not result.valid
    assert any("Forbidden function: read_parquet" in error for error in result.errors)


def test_rejects_unknown_tables() -> None:
    result = validate_query("SELECT 1 AS value")
    assert not result.valid
    assert any("lake dataset table" in error for error in result.errors)


def test_requires_voter_roll_limit() -> None:
    result = validate_query("SELECT uid FROM voter_roll_ge15")
    assert not result.valid
    assert any("voter_roll" in error for error in result.errors)


def test_rejects_voter_roll_limit_above_cap() -> None:
    result = validate_query("SELECT uid FROM voter_roll_ge15 LIMIT 10001")
    assert not result.valid
    assert any("voter_roll" in error for error in result.errors)


def test_accepts_voter_roll_with_limit() -> None:
    result = validate_query("SELECT uid FROM voter_roll_ge15 LIMIT 10000")
    assert result.valid
    assert result.tables == ["voter_roll_ge15"]


def test_strips_sql_comments() -> None:
    sql = """
    -- leading comment
    SELECT seat
    FROM headline_stats /* inline */
    LIMIT 1
    """
    result = validate_query(sql)
    assert result.valid
    assert result.tables == ["headline_stats"]


def test_referenced_tables_preserves_order() -> None:
    sql = "SELECT * FROM saluran_stats_ge15 JOIN saluran_ballots_ge15 USING (seat)"
    assert referenced_tables(sql) == ["saluran_stats_ge15", "saluran_ballots_ge15"]
