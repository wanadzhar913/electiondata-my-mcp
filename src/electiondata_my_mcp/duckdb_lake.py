#!/usr/bin/env python3
"""Query the ElectionData.MY data lake with DuckDB, outside the browser.

The Query Builder on electiondata.my runs DuckDB-WASM in the page and registers
the lake's Parquet files under friendly table names. This module does the same
thing locally, so SQL written against
src/components/tools/query-builder/copy-prompt.md runs unchanged here.

The lake is public — no API key, CORS open, HTTP range requests supported — so
httpfs reads only the row groups and columns a query actually touches.

Table name -> URL mapping mirrors src/components/tools/query-builder/datasets.ts,
which is the source of truth. Update both together.

Usage:
    # one-off query, pretty-printed
    python -m electiondata_my_mcp.duckdb_lake \\
        "SELECT seat, majority FROM headline_stats ORDER BY majority LIMIT 5"

    # from a file or stdin, as CSV or JSON
    python -m electiondata_my_mcp.duckdb_lake -f query.sql --format csv
    pbpaste | python -m electiondata_my_mcp.duckdb_lake --format json

    # cache the small tables locally so repeat queries hit disk, not the network
    python -m electiondata_my_mcp.duckdb_lake --cache lake.duckdb "SELECT ..."

    # as a library
    from electiondata_my_mcp.duckdb_lake import connect
    df = connect().sql("SELECT * FROM headline_ballots LIMIT 10").df()

Requires: pip install duckdb
"""

from __future__ import annotations

import argparse
import sys

LAKE = "https://lake.electiondata.my"

DATASETS: dict[str, str] = {
    "headline_ballots": f"{LAKE}/results_headline/headline_ballots.parquet",
    "headline_stats": f"{LAKE}/results_headline/headline_stats.parquet",
    "voter_demographics": f"{LAKE}/seat_info/demographics.parquet",
    "voter_demographics_sarawak": f"{LAKE}/seat_info/demographics_sarawak.parquet",
    "voter_demographics_sabah": f"{LAKE}/seat_info/demographics_sabah.parquet",
    "saluran_ballots_ge15": f"{LAKE}/results_saluran/ge15_ballots.parquet",
    "saluran_ballots_ge14": f"{LAKE}/results_saluran/ge14_ballots.parquet",
    "saluran_ballots_ge13": f"{LAKE}/results_saluran/ge13_ballots.parquet",
    "saluran_ballots_ge12": f"{LAKE}/results_saluran/ge12_ballots.parquet",
    "saluran_ballots_jhr_se16": f"{LAKE}/results_saluran/jhr_se16_ballots.parquet",
    "saluran_ballots_nsn_se15": f"{LAKE}/results_saluran/nsn_se15_ballots.parquet",
    "saluran_ballots_jhr_se15": f"{LAKE}/results_saluran/jhr_se15_ballots.parquet",
    "saluran_stats_ge15": f"{LAKE}/results_saluran/ge15_stats.parquet",
    "saluran_stats_ge14": f"{LAKE}/results_saluran/ge14_stats.parquet",
    "saluran_stats_ge13": f"{LAKE}/results_saluran/ge13_stats.parquet",
    "saluran_stats_ge12": f"{LAKE}/results_saluran/ge12_stats.parquet",
    "saluran_stats_jhr_se16": f"{LAKE}/results_saluran/jhr_se16_stats.parquet",
    "saluran_stats_nsn_se15": f"{LAKE}/results_saluran/nsn_se15_stats.parquet",
    "saluran_stats_jhr_se15": f"{LAKE}/results_saluran/jhr_se15_stats.parquet",
    "voter_roll_ge15": f"{LAKE}/voter_rolls/ge15_2022.parquet",
    "voter_roll_ge14": f"{LAKE}/voter_rolls/ge14_2018.parquet",
    "voter_roll_ge13": f"{LAKE}/voter_rolls/ge13_2013.parquet",
    "voter_roll_ge12": f"{LAKE}/voter_rolls/ge12_2008.parquet",
    "voter_roll_nsn_se16": f"{LAKE}/voter_rolls/nsn_se16_2026.parquet",
    "voter_roll_jhr_se16": f"{LAKE}/voter_rolls/jhr_se16_2026.parquet",
    "voter_roll_nsn_se15": f"{LAKE}/voter_rolls/nsn_se15_2023.parquet",
    "voter_roll_jhr_se15": f"{LAKE}/voter_rolls/jhr_se15_2022.parquet",
}

# Voter rolls run to ~22 million rows — always stream them over HTTP rather than
# materialising. Mirrors LAZY_DATASETS in the electiondata-my/meco-front's datasets.ts.
LAZY = {name for name in DATASETS if name.startswith("voter_roll_")}


def connect(db_path: str = ":memory:", cache: bool = False):
    """Return a DuckDB connection with every lake dataset registered.

    With cache=True the non-voter-roll tables are materialised into db_path on
    first use, so later queries read from disk instead of the network. Pass a
    real file path for that to persist across runs.
    """
    import duckdb

    con = duckdb.connect(db_path)
    con.execute("INSTALL httpfs; LOAD httpfs;")

    existing = {row[0] for row in con.execute("SHOW TABLES").fetchall()}

    for name, url in DATASETS.items():
        if cache and name not in LAZY:
            if name not in existing:
                con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{url}')")
        else:
            con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{url}')")

    return con


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run SQL against the ElectionData.MY data lake via DuckDB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Table names match copy-prompt.md. Run with --tables to list them.",
    )
    parser.add_argument("sql", nargs="?", help="SQL to run; omit to read stdin")
    parser.add_argument("-f", "--file", help="read SQL from a file")
    parser.add_argument(
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="output format (default: table)",
    )
    parser.add_argument(
        "--cache",
        metavar="DB_PATH",
        help="materialise non-voter-roll tables into DB_PATH for fast reuse",
    )
    parser.add_argument("--tables", action="store_true", help="list table names and exit")
    args = parser.parse_args()

    if args.tables:
        for name, url in DATASETS.items():
            marker = " (streamed, large)" if name in LAZY else ""
            print(f"{name}{marker}\n    {url}")
        return 0

    if args.file:
        with open(args.file) as handle:
            sql = handle.read()
    elif args.sql:
        sql = args.sql
    elif not sys.stdin.isatty():
        sql = sys.stdin.read()
    else:
        parser.error("provide SQL as an argument, via --file, or on stdin")

    if not sql.strip():
        parser.error("no SQL provided")

    con = connect(db_path=args.cache or ":memory:", cache=bool(args.cache))
    result = con.sql(sql)

    if result is None:
        return 0

    if args.format == "table":
        result.show(max_rows=100)
    elif args.format == "csv":
        writer = __import__("csv").writer(sys.stdout)
        writer.writerow(result.columns)
        writer.writerows(result.fetchall())
    else:
        import json

        columns = result.columns
        rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        print(json.dumps(rows, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
