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


def _strip_comments(sql: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", without_block)


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

    return ValidationResult(
        valid=not errors,
        tables=tables,
        errors=errors,
        warnings=warnings,
    )
