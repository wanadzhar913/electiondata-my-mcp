# ElectionData.MY v1 reference

Base URL: `https://api.electiondata.my/v1`

All endpoints are authenticated `GET` requests.

## Candidates

### `/candidates/dropdown`

No parameters. Returns an array of candidates ordered by contests, with:

- `uid`, `name`
- `c` contests, `w` wins, `l` losses

Use `uid` in `/candidates`.

### `/candidates`

Required: `uid`

Returns an array ordered newest to oldest. Each row identifies the candidate, election, seat, state, party and coalition, votes, vote percentage, polling date, and result.

Result values: `won`, `won_uncontested`, `lost`, `lost_deposit`.

## Seats

### `/seats/dropdown`

No parameters. Response: `{ "seats": [...] }`

Each row contains:

- `seat`: full current seat name
- `slug`: identifier for `/seats/results`
- `type`: `parlimen` or `dun`

### `/seats/results`

Required: `slug`

Optional: `lineage=true|false` (default `false`)

Response: `{ "results": [...] }`, newest first.

Election rows include `election_name`, `seat`, `state`, `date`, winner `name`, party and coalition identifiers, majority, and turnout.

With `lineage=true`, the array also contains boundary-change rows with `date`, `change_en`, and `change_ms`. A row with `election_name` is an election result; a row without it is a boundary-change event.

## Parties and coalitions

### `/parties/dropdown`

No parameters. Response: `{ "data": [...] }`

Each row contains `type`, `uid`, `maps_to`, `acronym`, `name_en`, and `name_bm`.

Historical names may have legacy UIDs. Always pass `maps_to`, not `uid`, to `/parties/results`.

### `/parties/results`

Required:

- `type`: `party` or `coalition`
- `uid`: the dropdown row's `maps_to`
- `state`
- `election_type`: `parlimen` or `dun`

Valid states:

`Malaysia`, `Semenanjung`, `Johor`, `Kedah`, `Kelantan`, `Melaka`, `Negeri Sembilan`, `Pahang`, `Perak`, `Perlis`, `Pulau Pinang`, `Sabah`, `Sarawak`, `Selangor`, `Terengganu`, `W.P. Kuala Lumpur`, `W.P. Labuan`, `W.P. Putrajaya`

`dun` is invalid for `Malaysia`, `Semenanjung`, and every `W.P. *` state.

Response: `{ "results": [...] }`, newest first. Rows contain historical identity (`known_as_uid`, `known_as`), election and date, seats contested and won, seat percentages, votes, and vote percentage. An empty `results` array is valid.

## Elections

### `/elections/dropdown`

No parameters. Response: `{ "elections": [...] }`

Each row contains `state`, `type`, `election`, and `date`. Pass `state` and `election` directly to the following election endpoints.

### `/elections/by_party`

Required: `state`, `election`

Response: `{ "by_party": [...] }`, ordered by votes descending. Rows include party and coalition identifiers, seats contested and won, total seats, seat percentages, votes, total valid votes, and vote percentage.

### `/elections/by_seat`

Required: `state`, `election`

Response: `{ "by_seat": [...] }`, one row per constituency. Rows contain the seat, date, winner, winning and losing parties and coalitions, candidate count, registered voters, turnout, majority, and rejected-vote statistics.

### `/elections/stats`

Required: `state`, `election`

Response: `{ "stats": [<single aggregate row>] }`

The row contains registered voters, turnout, turnout percentage, rejected votes, rejected-vote percentage, and candidate count.

## By-elections

### `/byelections`

No parameters. Response: `{ "data": [...] }`, newest first.

Each row contains `seat`, `state`, `date`, winner, party and coalition identifiers, registered voters, turnout, rejected votes, majority, and corresponding percentages.

Use a row's `seat`, `state`, and `date` to request full contest details from `/results`.

## Contest details

### `/results`

Required:

- `seat`
- `state`
- `date` in `YYYY-MM-DD`

Obtain all three values from candidates, seats, elections, or by-elections responses and pass them through unchanged.

Response:

- `ballot`: candidates ordered by votes descending, with party, coalition, votes, vote percentage, and result
- `stats`: a one-row array containing date, registered voters, turnout, rejected votes, majority, and corresponding percentages

For uncontested wins, vote counts may be zero and percentage statistics may be null.

## Common examples

Find a candidate's history:

```bash
curl --silent --show-error --fail-with-body --get \
  "https://api.electiondata.my/v1/candidates" \
  --header "Authorization: Bearer ${ELECTIONDATAMY_API_KEY}" \
  --data-urlencode "uid=CMVBA"
```

Get election results by party:

```bash
curl --silent --show-error --fail-with-body --get \
  "https://api.electiondata.my/v1/elections/by_party" \
  --header "Authorization: Bearer ${ELECTIONDATAMY_API_KEY}" \
  --data-urlencode "state=Malaysia" \
  --data-urlencode "election=GE-15"
```

Get one contest:

```bash
curl --silent --show-error --fail-with-body --get \
  "https://api.electiondata.my/v1/results" \
  --header "Authorization: Bearer ${ELECTIONDATAMY_API_KEY}" \
  --data-urlencode "seat=P.001 Padang Besar" \
  --data-urlencode "state=Perlis" \
  --data-urlencode "date=2022-11-19"
```
