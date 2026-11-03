"""Human-readable dataset metadata for MCP discovery tools.

Update entries here when adding lake tables (see CONTRIBUTING.md). The bundled
query guide remains the full schema reference; these strings are short hints
with coverage dates and common pitfalls.

Lookup tables are an MCP-only extension (meco-etl 55c6098). Column detail lives
in prompts/lookup-tables-appendix.md. Do not copy them into the bundled
upstream query-builder-prompt.md.
"""

from __future__ import annotations

from electiondata_my_mcp.duckdb_lake import DATASETS

SNAPSHOT_LABELS: dict[str, str] = {
    "ge15": "GE-15 (19 Nov 2022)",
    "ge14": "GE-14 (9 May 2018)",
    "ge13": "GE-13 (5 May 2013)",
    "ge12": "GE-12 (8 March 2008)",
    "jhr_se16": "Johor SE-16 (11 Jul 2026)",
    "nsn_se16": "Negeri Sembilan SE-16 (1 Aug 2026)",
    "nsn_se15": "Negeri Sembilan SE-15 (2023)",
    "jhr_se15": "Johor SE-15 (2022)",
}

STATE_ELECTION_TRAP = (
    "If filtering election = 'SE-*', also filter state "
    "(e.g. SE-16 includes both Johor and Negeri Sembilan)."
)

VOTER_ROLL_LIMIT_NOTE = "Queries must include LIMIT ≤10000."

CORE_DATASETS: dict[str, tuple[str, str]] = {
    "headline_ballots": (
        "Candidate results; coverage GE-01 through GE-15 and all state elections "
        "through JHR/NSN SE-16 (2026). " + STATE_ELECTION_TRAP,
        "Every contest and candidate: votes, parties, results.",
    ),
    "headline_stats": (
        "Seat statistics; coverage GE-01 through GE-15 and all state elections "
        "through JHR/NSN SE-16 (2026). " + STATE_ELECTION_TRAP,
        "Electorate and turnout per seat: voters_total is registered voters as of that election.",
    ),
    "voter_demographics": (
        "Seat-level demographics (sex, age, ethnicity); GE-13, GE-14, GE-15 nationwide, "
        "plus Johor SE-15/SE-16 and Negeri Sembilan SE-16. " + STATE_ELECTION_TRAP,
        "Seat-level voter counts by demographic column (not multi-dimension cross-tabs).",
    ),
    "voter_demographics_sarawak": (
        "Sarawak seat demographics with Sarawak ethnic groups; GE-13, GE-14, GE-15.",
        "Sarawak-only seat demographics.",
    ),
    "voter_demographics_sabah": (
        "Sabah seat demographics with Sabah ethnic groups; GE-13, GE-14, GE-15.",
        "Sabah-only seat demographics.",
    ),
}

# meco-etl 55c6098 (2026-11-01) uploaded these under results_headline/.
# meco-front datasets.ts does not register them.
MCP_LOOKUP_NOTE = (
    "MCP-specific extension beyond upstream meco-front datasets.ts "
    "(meco-etl 55c6098); queryable in this server only."
)

LOOKUP_DATASETS: dict[str, tuple[str, str]] = {
    "lookup_candidate": (
        "One row per candidate_uid: name, sex (M/F), ethnicity, and dob stored as "
        "text (unknown parts are xx or xxxx, so this is not a DATE). " + MCP_LOOKUP_NOTE,
        "Resolve candidate_uid to a stable name and date of birth; "
        "join headline_ballots on candidate_uid.",
    ),
    "lookup_coalition": (
        "Coalition dictionary: coalition_uid, code, English and Malay names, "
        "year_start, and year_end (-1 if open-ended). " + MCP_LOOKUP_NOTE,
        "Map coalition_uid or coalition codes to full names; prefer this over the "
        "static coalition dictionary in the Query Builder prompt.",
    ),
    "lookup_coalition_succession": (
        "Coalition lineage edges: predecessor_uid, successor_uid, type, year, and "
        "notes (PERIKATAN to BN, BA to PR, PR to PH). " + MCP_LOOKUP_NOTE,
        "Follow coalition replacements that share a numeric uid prefix.",
    ),
    "lookup_dates": (
        "Polling dates for general and state elections (by-elections are "
        "lookup_prk): state (Malaysia means federal), election_number, and date. "
        + MCP_LOOKUP_NOTE,
        "Look up a GE or SE polling date; pair election_number with state because "
        "the same number repeats across states.",
    ),
    "lookup_party": (
        "Party dictionary: party_uid, ballot code, English and Malay names, "
        "year_formed, and year_end (-1 if still active). " + MCP_LOOKUP_NOTE,
        "Map party_uid or party codes to full names; prefer this over the static "
        "party dictionary in the Query Builder prompt.",
    ),
    "lookup_party_succession": (
        "Party lineage edges: predecessor_uid, successor_uid, type "
        "(replace, merge, absorb, splinter, split), year, and notes. " + MCP_LOOKUP_NOTE,
        "Follow renames, mergers, absorptions, and splinters across party_uid values.",
    ),
    "lookup_prk": (
        "By-elections (PRK): date, state, seat, trigger (death, resignation, "
        "voided_by_court, and others), and notes. Historical seat spellings do "
        "not always match headline_ballots. " + MCP_LOOKUP_NOTE,
        "Explain why a by-election was held; join headline tables on date, state, "
        "and seat where election = 'BY-ELECTION'.",
    ),
}


def _snapshot_suffix(name: str, prefix: str) -> str | None:
    if not name.startswith(prefix):
        return None
    return name.removeprefix(prefix)


def dataset_description(name: str) -> str:
    if name in CORE_DATASETS:
        return CORE_DATASETS[name][0]
    if name in LOOKUP_DATASETS:
        return LOOKUP_DATASETS[name][0]

    suffix = _snapshot_suffix(name, "saluran_ballots_")
    if suffix is not None:
        label = SNAPSHOT_LABELS.get(suffix, suffix)
        return f"Saluran-level candidate ballots, {label}."

    suffix = _snapshot_suffix(name, "saluran_stats_")
    if suffix is not None:
        label = SNAPSHOT_LABELS.get(suffix, suffix)
        return f"Saluran-level statistics, {label}."

    suffix = _snapshot_suffix(name, "voter_roll_")
    if suffix is not None:
        label = SNAPSHOT_LABELS.get(suffix, suffix)
        extra = ""
        if suffix == "nsn_se16":
            extra = " No matching saluran_ballots_* or saluran_stats_* tables."
        return f"Voter roll snapshot, {label}. {VOTER_ROLL_LIMIT_NOTE}{extra}"

    return "ElectionData.MY lake dataset."


def dataset_use_for(name: str) -> str:
    if name in CORE_DATASETS:
        return CORE_DATASETS[name][1]
    if name in LOOKUP_DATASETS:
        return LOOKUP_DATASETS[name][1]

    if name.startswith("saluran_ballots_"):
        return (
            "Candidate votes per saluran; join to voter_roll_* on dm, pm, saluran "
            "(not available for voter_roll_nsn_se16)."
        )
    if name.startswith("saluran_stats_"):
        return "Turnout and ballot statistics per saluran."
    if name.startswith("voter_roll_"):
        if name == "voter_roll_nsn_se16":
            return "N9 SE-16 roll snapshot only; use for demographics or dun-level aggregates (with LIMIT)."  # noqa: E501
        return "Voter-level demographics and saluran alignment; aggregate before joining saluran tables."  # noqa: E501
    return "See get_query_guide for schema and join rules."


def assert_catalog_covers_datasets() -> None:
    missing = [
        name for name in DATASETS if dataset_description(name) == "ElectionData.MY lake dataset."
    ]
    if missing:
        raise RuntimeError(f"dataset_catalog missing metadata for: {', '.join(missing)}")
