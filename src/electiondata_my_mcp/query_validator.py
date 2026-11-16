"""Validate read-only SQL against the ElectionData.MY lake table allowlist."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from electiondata_my_mcp.duckdb_lake import DATASETS

FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "COPY",
    "ATTACH",
    "DETACH",
    "INSTALL",
    "LOAD",
    "EXPORT",
    "IMPORT",
    "CALL",
    "EXECUTE",
    "PREPARE",
    "PRAGMA",
    "SET",
    "RESET",
    "VACUUM",
    "ANALYZE",
)

FORBIDDEN_FUNCTIONS = (
    "read_parquet",
    "read_csv",
    "read_json",
    "read_blob",
    "glob",
    "httpfs",
)

VOTER_ROLL_LIMIT = 10_000
TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(name) for name in sorted(DATASETS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
LIMIT_PATTERN = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
SE_ELECTION_FILTER_PATTERN = re.compile(r"\belection\s*=\s*'SE-", re.IGNORECASE)
STATE_FILTER_PATTERN = re.compile(r"\bstate\s*(=|IN\b|LIKE\b)", re.IGNORECASE)
STATE_ELECTION_TABLES = frozenset(
    {"headline_ballots", "headline_stats", "voter_demographics"},
)
KEYWORD_PATTERN = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
FUNCTION_PATTERN = re.compile(
    r"\b(" + "|".join(FORBIDDEN_FUNCTIONS) + r")\s*\(",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    valid: bool
    tables: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _token_boundary(sql: str, index: int) -> bool:
    if index == 0:
        return True
    previous = sql[index - 1]
    return not (previous.isalnum() or previous == "_")


def _copy_quoted(sql: str, index: int, out: list[str], *, backslash: bool) -> int:
    """Copy one `'...'` or `"..."` literal, starting at its opening quote.

    Standard strings and quoted identifiers escape a quote by doubling it.
    ``E'...'`` strings also escape with a backslash, including ``\\'``.
    """
    quote = sql[index]
    out.append(quote)
    index += 1
    limit = len(sql)
    while index < limit:
        char = sql[index]
        if backslash and char == "\\":
            out.append(char)
            if index + 1 < limit:
                out.append(sql[index + 1])
                index += 2
            else:
                index += 1
            continue
        out.append(char)
        if char == quote:
            if index + 1 < limit and sql[index + 1] == quote:
                out.append(quote)
                index += 2
                continue
            return index + 1
        index += 1
    return index


def _dollar_literal_end(sql: str, index: int) -> int | None:
    """Return the index after a ``$tag$...$tag$`` literal, or None.

    Tags match DuckDB: empty, or a letter/underscore plus word characters
    (Unicode included). An opening delimiter with no closer consumes the
    rest; the statement check still sees any ``;`` because the body is copied.
    """
    if sql[index] != "$":
        return None
    cursor = index + 1
    if cursor < len(sql) and (sql[cursor].isalpha() or sql[cursor] == "_"):
        cursor += 1
        while cursor < len(sql) and (sql[cursor].isalnum() or sql[cursor] == "_"):
            cursor += 1
    if cursor >= len(sql) or sql[cursor] != "$":
        return None
    tag = sql[index : cursor + 1]
    close = sql.find(tag, cursor + 1)
    if close == -1:
        return len(sql)
    return close + len(tag)


def _strip_comments(sql: str) -> str:
    """Drop DuckDB comments without eating text inside literals.

    ``/*`` and ``--`` inside a string or quoted identifier are data. Stripping
    them as comments hides a later statement from the checks below while
    ``DuckDBPyConnection.sql`` still runs it.
    """
    out: list[str] = []
    index = 0
    limit = len(sql)
    while index < limit:
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            out.append(" ")
            if newline == -1:
                break
            index = newline
            continue
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            out.append(" ")
            if end == -1:
                break
            index = end + 2
            continue
        char = sql[index]
        escape_string = (
            char in "Ee"
            and index + 1 < limit
            and sql[index + 1] == "'"
            and _token_boundary(sql, index)
        )
        if escape_string:
            out.append(char)
            index = _copy_quoted(sql, index + 1, out, backslash=True)
            continue
        if char in "'\"":
            index = _copy_quoted(sql, index, out, backslash=False)
            continue
        dollar_end = _dollar_literal_end(sql, index)
        if dollar_end is not None:
            out.append(sql[index:dollar_end])
            index = dollar_end
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _normalize(sql: str) -> str:
    return " ".join(_strip_comments(sql).split())


def referenced_tables(sql: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in TABLE_REFERENCE_PATTERN.finditer(sql):
        name = match.group(1).lower()
        canonical = next(key for key in DATASETS if key.lower() == name)
        if canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)
    return ordered


def validate_query(sql: str) -> ValidationResult:
    """Return validation details for a DuckDB SELECT query."""
    errors: list[str] = []
    warnings: list[str] = []

    if not sql or not sql.strip():
        return ValidationResult(valid=False, errors=["SQL must not be empty"])

    normalized = _normalize(sql)
    upper = normalized.upper()

    if ";" in normalized.rstrip(";"):
        errors.append("Only a single SQL statement is allowed")

    if not (upper.startswith("SELECT ") or upper.startswith("WITH ")):
        errors.append("Only SELECT queries are allowed")

    for match in KEYWORD_PATTERN.finditer(normalized):
        errors.append(f"Forbidden keyword: {match.group(1).upper()}")

    for match in FUNCTION_PATTERN.finditer(normalized):
        errors.append(f"Forbidden function: {match.group(1)}")

    tables = referenced_tables(normalized)
    if not tables:
        errors.append("Query must reference at least one lake dataset table")

    voter_roll_tables = [name for name in tables if name.startswith("voter_roll_")]
    if voter_roll_tables:
        limits = [int(value) for value in LIMIT_PATTERN.findall(normalized)]
        if not limits or max(limits) > VOTER_ROLL_LIMIT:
            errors.append(
                f"Queries using voter_roll_* tables must include LIMIT {VOTER_ROLL_LIMIT} or less"
            )

    if (
        any(name in STATE_ELECTION_TABLES for name in tables)
        and SE_ELECTION_FILTER_PATTERN.search(normalized)
        and not STATE_FILTER_PATTERN.search(normalized)
    ):
        warnings.append(
            "State election filter detected (election = 'SE-*') without a state filter; "
            "the same SE code can appear in multiple states — add AND state = '...'."
        )

    return ValidationResult(
        valid=not errors,
        tables=tables,
        errors=errors,
        warnings=warnings,
    )
