---
name: query-electiondatamy-api
description: Queries and analyzes Malaysian election data through the ElectionData.MY API. Use when the user asks for candidates, seats, parties, coalitions, elections, by-elections, contest results, turnout, majorities, or vote shares from ElectionData.MY.
---

# Query ElectionData.MY API

Use the v1 API to answer focused questions about Malaysian election data. Read [reference.md](reference.md) before choosing endpoints or parameters.

## Scope

Use the API for focused, dynamic queries. Recommend the ElectionData.MY data lake instead when the user needs:

- bulk analysis or the full dataset;
- geospatial or boundary files;
- local analytical workflows better served by static files.

## Authentication and safety

- Base URL: `https://api.electiondata.my/v1`
- Only `GET` requests are supported.
- Every request requires `Authorization: Bearer <API key>`.
- Use the `ELECTIONDATAMY_API_KEY` environment variable. Check that it exists without printing it:

```bash
test -n "$ELECTIONDATAMY_API_KEY"
```

- If it is missing, ask the user to set it in their terminal. Do not ask them to paste the key into chat.
- Never print, persist, commit, or interpolate the key as a literal.

## Query workflow

1. Translate the question into the entity, scope, election type, and date needed.
2. Select the endpoint chain from the reference.
3. When an identifier is unknown, call the relevant dropdown endpoint first. Do not guess UIDs, slugs, election identifiers, seat names, states, or dates.
4. Pass identifiers returned by discovery endpoints exactly as documented:
   - Candidate queries use `uid`.
   - Party queries use the selected entry's `maps_to` as `uid`, never its historical `uid`.
   - Result detail queries reuse `seat`, `state`, and `date` from a companion endpoint without transformation.
5. URL-encode every query parameter. Prefer `curl --get --data-urlencode`.
6. Inspect the complete response before filtering or aggregating it.
7. Answer concisely, naming the endpoint and query scope used. Clearly separate returned fields from calculations or interpretation.

## Request pattern

```bash
curl --silent --show-error --fail-with-body --get \
  "https://api.electiondata.my/v1/<endpoint>" \
  --header "Authorization: Bearer ${ELECTIONDATAMY_API_KEY}" \
  --data-urlencode "parameter=value"
```

For an endpoint without parameters, omit `--get` and `--data-urlencode`.

## Endpoint chaining

- Candidate lookup: `/candidates/dropdown` → `/candidates?uid=...` → optionally `/results`
- Seat history: `/seats/dropdown` → `/seats/results?slug=...` → optionally `/results`
- Party or coalition history: `/parties/dropdown` → `/parties/results`
- Election overview: `/elections/dropdown` → one or more of `/elections/by_party`, `/elections/by_seat`, `/elections/stats`
- By-election list: `/byelections` → optionally `/results`
- Contest detail: obtain `seat`, `state`, and `date` from a companion endpoint → `/results`

## Interpretation rules

- Treat ISO dates as polling dates unless the field explicitly describes a boundary-change event.
- Percentage fields are already percentages; do not multiply them by 100.
- `won_uncontested` can legitimately produce zero or null vote statistics.
- In party queries, `{"results":[]}` means no contests in that scope, not an API error.
- With seat `lineage=true`, distinguish election rows by the presence of `election_name`; rows without it are boundary-change events.
- Preserve the API's `parlimen` and `dun` values when filtering.
- Do not infer current party identity from `known_as`; explain historical names and canonical mappings when relevant.

## Errors

- `400`: missing or invalid parameters, invalid combinations, malformed date, or missing version prefix.
- `401`: missing or invalid API key.
- `404`: unknown endpoint or no matching resource.
- `405`: method other than `GET`.
- `500`: server-side failure.

Error bodies contain an `error` field. Report the useful message, correct the request when possible, and do not silently reinterpret an invalid query.

## Answer quality

- State material filters such as state, election, election type, lineage mode, and date.
- Include units for vote counts, seats, turnout, and percentages.
- For comparisons, verify that scopes and denominators match.
- Mention missing or null data rather than silently dropping it.
- Do not claim the API is real-time; dropdown datasets update around election-related publication events described in the docs.
