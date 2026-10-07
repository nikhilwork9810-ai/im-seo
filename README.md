# im-seo

Keyword rank tracking for hospitality clients, built on the DataForSEO SERP API.

`rank_tracker.py` queues one Google organic SERP task per keyword per device, waits
for the queue to finish, and writes the client domain's position for every keyword
to a CSV.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your DataForSEO login and password
```

`.env` is gitignored. The script reads `DFS_LOGIN` and `DFS_PASSWORD` from the
environment and exits with a clear message if either is missing. No credentials
belong in the source files.

## Running it

```bash
python rank_tracker.py --target-domain peerlesshotels.com
```

That reads `keywords.csv`, runs every keyword on desktop and mobile, and writes
`rankings_peerlesshotels_com_<timestamp>.csv`.

Check what would be sent without spending any credits:

```bash
python rank_tracker.py --dry-run
```

Useful options:

| Option | Default | What it does |
| --- | --- | --- |
| `--target-domain` | `peerlesshotels.com` | Domain to look for in the SERPs |
| `--keywords` | `keywords.csv` | Input file |
| `--out` | timestamped filename | Output file |
| `--devices` | `desktop,mobile` | Comma-separated; use `desktop` alone to halve the cost |
| `--depth` | `100` | How many organic results to scan per SERP |
| `--poll-interval` | `30` | Seconds between queue checks |
| `--max-wait` | `2700` | Seconds before giving up on unfinished tasks |
| `--dry-run` | off | Print the queries, call nothing |

## Input format

`keywords.csv` needs three columns:

```csv
keyword,location_code,location_label
best hotel in durgapur,9040193,"Durgapur,West Bengal,India"
```

`location_label` is only for readability in the output. `location_code` is what the
API actually uses. Anyone on the team can edit this file without touching the Python.

Duplicate keyword and location pairs are skipped with a warning.

One file per client. `keywords.csv` holds the Peerless Hotels set; `keywords_maplebear.csv`
holds Maple Bear Gulf Schools, Town Square Dubai. Point `--keywords` at whichever you need
and set `--target-domain` to match.

### Location codes in use

Every keyword in the current list carries a city modifier, so each one runs from its
target city rather than from a generic India-wide location. These codes were pulled
from `/v3/serp/google/locations` for `IN`:

| Location | Type | Code | Keywords |
| --- | --- | --- | --- |
| Durgapur, West Bengal, India | City | 9040193 | 20 |
| Hyderabad, Telangana, India | City | 1007740 | 17 |
| Kolkata, Kolkata, West Bengal, India | City | 1007828 | 14 |
| India | Country | 2356 | not used by default |

For Maple Bear Gulf Schools (`keywords_maplebear.csv`), all 12 keywords run from
`Dubai,Dubai,United Arab Emirates`, code `1000013`, which resolves to google.ae. Town Square
is a Nshama community off Al Qudra Road and has no location code of its own in the DataForSEO
database, so Dubai city is the closest available targeting.

Gachibowli has no location code of its own in the DataForSEO location database, so
the 14 Gachibowli keywords run from Hyderabad city. That is the closest available
targeting. Positions for those keywords reflect a Hyderabad-wide searcher, not
someone standing in Gachibowli.

To report on national visibility instead, set every `location_code` to `2356`.
Expect different numbers: local intent keywords resolve differently at country level.

## Output format

| Column | Meaning |
| --- | --- |
| `keyword`, `device`, `location_code`, `location_label` | What was queried |
| `status` | `ranked`, `not_in_top_<depth>`, `api_error`, `queue_failed`, or `timeout` |
| `rank_group` | Position counting organic results only |
| `rank_absolute` | Position counting everything on the page |
| `ranking_url` | The URL that ranked |
| `task_id` | DataForSEO task ID, for support queries |
| `checked_at` | UTC timestamp |

Both position columns are recorded because they answer different questions.
`rank_group` is comparable to what other rank trackers report. `rank_absolute` is
what the searcher actually experiences. On hotel keywords the gap is often large:
for "hotels in durgapur" from India, peerlesshotels.com sat at organic 9 but on-page
13, with hotels packs and a People Also Ask block occupying the difference. Reporting
only `rank_group` hides that.

Every query produces exactly one row. A keyword that does not rank is recorded as
`not_in_top_<depth>`, which is a real finding, and is kept distinct from `api_error`,
which means the check did not happen and should be re-run.

## Notes on cost and runtime

51 keywords across two devices is 102 SERP tasks per run. The script uses the
standard task queue, which is the cheapest option and takes a few minutes to drain.
Rows are flushed to the output CSV as each task comes back, so a run that is
interrupted still leaves usable partial results.

Check your balance and per-task pricing in the DataForSEO dashboard before large
runs. Pricing and endpoint behaviour change from time to time, so treat any figure
written down here or elsewhere as needing verification against the dashboard.
