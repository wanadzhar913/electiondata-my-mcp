## MCP-specific appendix: lookup tables

This appendix is added by the electiondata-my-mcp server. It is not part of the upstream Query Builder prompt (`copy-prompt.md`) and these tables are not registered in meco-front `datasets.ts`. [meco-etl commit 55c6098](https://github.com/electiondata-my/meco-etl/commit/55c6098) (2026-11-01) uploaded them to `results_headline/` on the public lake. SQL that names a `lookup_*` table runs in this server. The website Query Builder does not currently expose these table names.

Prefer these tables when a code, name, or year disagrees with the static Coalition Dictionary and Party Dictionary earlier in this guide. Those dictionaries are the upstream prompt snapshot.

All seven are small, non-lazy views (they can be materialised with `--cache`). Open-ended or inapplicable years are the integer `-1`, not NULL.

### `lookup_candidate`

One row per `candidate_uid`.

Columns:

- `candidate_uid` (VARCHAR): Stable candidate identifier. Join to `headline_ballots.candidate_uid` and `saluran_ballots_*.candidate_uid`.
- `candidate_rn` (BIGINT): Row number inside this file. Not a join key.
- `name` (VARCHAR): Normalised candidate name.
- `sex` (VARCHAR): `M` or `F`.
- `ethnicity` (VARCHAR): Same labels as `headline_ballots.ethnicity` (Malay, Chinese, Indian, Bumi Sarawak, Bumi Sabah, Orang Asli, Other).
- `dob` (VARCHAR): Date of birth as text. Unknown parts are `xx` or `xxxx`, for example `1914-08-06`, `1897-xx-xx`, or `xxxx-xx-xx`. Leave it as text; partial values are not valid dates.

### `lookup_party`

One row per `party_uid`. A rebrand that keeps the numeric prefix is still its own row, for example `006-PPP` and `006-MYPPP`.

Columns:

- `party_uid` (VARCHAR): Stable party identifier. Join to `headline_ballots.party_uid`.
- `party` (VARCHAR): Ballot abbreviation (`DAP`, `BEBAS`, …). Filter on this code, not on the English or Malay name.
- `party_name_en` (VARCHAR): English name.
- `party_name_bm` (VARCHAR): Bahasa Malaysia name.
- `year_formed` (BIGINT): Year the party (or that abbreviation) starts in this dictionary.
- `year_end` (BIGINT): Last year, or `-1` when the row is still current.
- `notes` (VARCHAR): Optional history. NULL when empty.

`000-BEBAS` is the independent-candidate row.

### `lookup_party_succession`

Directed edges between `party_uid` values.

Columns:

- `predecessor_uid` (VARCHAR): Earlier `party_uid`.
- `successor_uid` (VARCHAR): Later `party_uid`.
- `type` (VARCHAR): Relationship. Values in the current file: `splinter`, `replace`, `merge`, `absorb`, `split`.
- `year` (BIGINT): Year of the change.
- `notes` (VARCHAR): Short explanation.

A merge is several predecessor rows that share one `successor_uid`. Join either uid column to `lookup_party.party_uid` for names.

### `lookup_coalition`

One row per `coalition_uid`.

Columns:

- `coalition_uid` (VARCHAR): Stable coalition identifier. Join to `headline_ballots.coalition_uid`.
- `coalition` (VARCHAR): Code (`PH`, `BN`, `ALONE`, …). Filter on this code, not on the expanded name.
- `coalition_name_en` (VARCHAR): English name.
- `coalition_name_bm` (VARCHAR): Bahasa Malaysia name.
- `year_start` (BIGINT): First year, or `-1` when the year does not apply.
- `year_end` (BIGINT): Last year, or `-1` when the coalition is still current or the year does not apply.
- `notes` (VARCHAR): Short history.

`000-ALONE` is the sentinel for candidates outside a coalition. Its `year_start` and `year_end` are both `-1`. Successor coalitions still share a numeric prefix (`001-PERIKATAN` / `001-BN`, `007-BA` / `007-PR` / `007-PH`).

### `lookup_coalition_succession`

Directed edges between `coalition_uid` values. Same column layout as `lookup_party_succession` (`predecessor_uid`, `successor_uid`, `type`, `year`, `notes`). The current file only uses `type = 'replace'` (PERIKATAN to BN, BA to PR, PR to PH).

### `lookup_dates`

Polling dates for general elections and state elections. By-elections are in `lookup_prk`, not here.

Columns:

- `state` (VARCHAR): State name, matching `headline_ballots.state`, or `Malaysia` for a federal general election.
- `election_number` (BIGINT): Numeric part of `GE-n` / `SE-n`. `0` is the 1955 federal election (`GE-00` in the headline tables). The same number is reused for every state that held that election, so always filter `state` as well.
- `date` (DATE): Polling day. The file includes scheduled polls that may not have headline results yet.

Federal GE-15 is `state = 'Malaysia' AND election_number = 15` (2022-11-19). A state election such as Johor SE-16 is `state = 'Johor' AND election_number = 16`.

### `lookup_prk`

One row per by-election (PRK, pilihan raya kecil).

Columns:

- `date` (DATE): Polling day.
- `state` (VARCHAR): State name.
- `seat` (VARCHAR): Seat label, same `P.` / DUN convention as `headline_ballots.seat`.
- `trigger` (VARCHAR): Why the seat was vacated. Values in the current file: `death`, `resignation`, `voided_by_court`, `criminal_conviction`, `bankruptcy`, `anti_hopping`.
- `notes` (VARCHAR): Optional detail. NULL on most rows.

Join headline results on `date`, `state`, and `seat` with `election = 'BY-ELECTION'`. Historical seat spellings differ on some rows, so the join is incomplete in both directions.

```sql
SELECT b.seat, b.name, b.party, k.trigger
FROM headline_ballots AS b
JOIN lookup_prk AS k USING (date, state, seat)
WHERE b.election = 'BY-ELECTION' AND b.result = 'won'
```
