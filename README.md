# Global Air Quality Snapshot

A small end-to-end data pipeline that pulls recent outdoor air pollution
readings for ten world cities from the [OpenAQ](https://openaq.org) API,
cleans and lands them in DuckDB, and renders a static HTML report comparing
cities against the WHO air quality guidelines.

## About me and why I built this

Hi, I'm Melvin, a First Class Computer Science graduate with a strong interest in data engineering, AI and using technology to solve practical problems. I enjoy working with data from the point where it is collected through to the point where it can be trusted and used by someone else. This is what attracted me to data engineering: building the pipelines, transformations and data structures that make reliable analysis possible.

I built this project as an end-to-end data engineering exercise because I wanted to work with a real API and deal with the kinds of problems that occur in real datasets rather than use a clean, pre-prepared dataset. It gave me the opportunity to work with Python, APIs, data ingestion, data cleaning, data modelling, testing and DuckDB while making decisions about how the final data should be structured and validated.

I like breaking ambiguous problems into smaller steps, investigating unfamiliar data, finding reliable ways to transform it and explaining technical decisions clearly. The opportunity to develop further in areas such as SQL, Python, APIs, pipelines, data modelling and cloud technologies, and then apply those skills to real client problems, is exactly the direction I want to take my career.

Outside of technical work, I enjoy learning about new technologies and understanding how they can be applied to real-world problems. I am naturally curious and enjoy figuring out how things work, especially when the answer is not immediately obvious.

## Purpose

A small end-to-end data pipeline that pulls recent outdoor air pollution readings for ten world cities from the [OpenAQ](https://openaq.org) API, cleans and lands them in DuckDB, and renders a static HTML report comparing cities against the WHO air quality guidelines.

I wanted a project on a topic I actually check in real life (is the air bad today, is one city worse than another) rather than a toy dataset. Air quality data is also genuinely messy in the way this kind of exercise is supposed to surface: patchy global coverage, sensors that occasionally report impossible values, pollutants measured in inconsistent units depending on the provider, and gaps where a station goes offline for a day.

**Intended audience:** someone deciding where the air is currently breathable — a traveler comparing destinations, someone with asthma or allergies checking conditions, or anyone curious how their city stacks up. The report is written for that reader: a quick visual comparison against a health-based reference point, not a raw data dump.

## Data source

- **API:** OpenAQ v3 (`https://api.openaq.org/v3`) — a free, open platform
  aggregating physical air-quality sensor data from government and
  research monitors worldwide.
- **Auth:** free API key, sent as an `X-API-Key` header. Sign up at
  <https://explore.openaq.org>.
- **Rate limit:** 60 requests/minute on the free tier. The client enforces
  a fixed delay between requests and backs off on HTTP 429.
- **Endpoints used:**
  - `GET /v3/locations` — find the nearest monitoring station(s) to each
    city's coordinates, and which pollutants it senses.
  - `GET /v3/sensors/{id}/measurements/daily` — daily-aggregated readings
    per sensor, paginated via `page`/`limit`, using the response's
    `meta.found` to know when to stop.
- **Cities:** London, Paris, Berlin, New York, Los Angeles, Delhi, Beijing,
  São Paulo, Lagos, Sydney (chosen for a mix of monitoring density — some
  of these, like Lagos, have sparse coverage on purpose, to make the
  "missing data" handling meaningful rather than theoretical).
- **Pollutants:** PM2.5, PM10, NO₂, O₃ — the four most commonly monitored
  and most relevant to daily-life "is the air bad" questions.

## Pipeline

```
data/raw/               <- exact, untouched API responses (bronze layer)
  locations/{iso}_{city}.json
  measurements/{city}_{parameter}_sensor{id}.json
  manifest.json          <- what was fetched and when
        |
        v
src/transform.py         <- cleaning + modeling (see decisions below)
        |
        v
data/processed/air_quality.duckdb
  dim_locations           <- one row per monitoring station used
  fact_daily_air_quality  <- one row per (sensor, date)
        |
        v
src/build_report.py
        |
        v
report/index.html         <- self-contained static report (charts inlined
                              as base64 PNGs, no external assets/server)
```

### `fetch_data.py` — getting the data

- Saves every raw API response to disk exactly as received, before any
  cleaning, so a transform bug never requires re-hitting the API.
- Handles pagination via `page`/`limit`, stopping once `meta.found` records
  have been collected.
- Throttles requests to stay under the 60/minute free-tier limit, and
  retries with backoff on `429` (respecting `Retry-After`) and `5xx`.
- Reads the API key from `OPENAQ_API_KEY` (via `.env`, which is
  gitignored) — never hardcoded, never committed.

### `transform.py` — shaping the data

Real decisions made here, because "just load it" wasn't good enough:

| Issue found in the raw data | Decision |
|---|---|
| `value` is sometimes JSON `null` (sensor outage) | Kept as SQL `NULL`, not `0` — a missing reading isn't the same as clean air |
| Some readings are negative (physically impossible for a concentration) | Treated as a sensor glitch and nulled out |
| Days with <75% of expected hourly readings | Treated as unreliable and nulled out, but the day is *kept* (not dropped) so gaps are visible and honestly reported |
| Ozone sometimes reported in `ppm` instead of `µg/m³` depending on the provider | Converted to `µg/m³` (×1960) so every row is comparable |
| Same (sensor, date) reading appearing twice across paginated API calls | Deduplicated, keeping the first occurrence |
| Raw JSON is deeply nested, camelCase | Flattened into two snake_case tables (`dim_locations`, `fact_daily_air_quality`) |
| Re-running the pipeline (new day's fetch, or retry after a failure) | Load is done inside a transaction as delete-then-insert per city, so reruns don't duplicate rows |

All of this is covered by `tests/test_transform.py`, which runs against
fixture JSON mirroring OpenAQ's real (documented) response schema — so the
cleaning logic is verified without needing network access.

### `build_report.py` — the deliverable

Queries DuckDB directly (no intermediate CSV), builds a bar chart per
pollutant comparing cities against the WHO 2021 guideline, a daily trend
line chart, and a data-completeness table so the report doesn't quietly
hide the fact that some cities have thin coverage. Everything — HTML, CSS,
and charts — is inlined into one `report/index.html` file so it opens
correctly on any machine with no server or internet connection.

## How to run

Requires Python 3.10+.

```bash
git clone <this-repo-url>
cd air-quality-pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your free API key from https://explore.openaq.org

./run_pipeline.sh
# or step by step:
#   python3 -m src.fetch_data
#   python3 -m src.transform
#   python3 -m src.build_report

open report/index.html   # or just double-click it
```

To run the tests (no API key or network needed):

```bash
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

This repo is intentionally clean-clone-able: `data/raw/`, `data/processed/`
and `report/*.html` are gitignored and regenerated by the pipeline, not
committed.

## Future improvements

- **Historical trend beyond 30 days** — currently pulls a rolling window;
  a scheduled run (e.g. GitHub Actions on a daily cron) accumulating a
  longer history would let the report show seasonal patterns.
- **More robust city→station matching** — currently picks the nearest
  station with the most target pollutants; a production version would let
  a city fall back across multiple stations to fill gaps rather than
  relying on a single sensor per pollutant.
- **Alerting** — a natural next step given the audience: a mode that emails
  or pushes a notification when a followed city crosses a guideline
  threshold, instead of only a point-in-time report.
- **Population-weighted context** — pairing readings with population data
  to communicate exposure impact, not just concentration.
- **Config-driven city list** — move `CITIES` out of `config.py` into a
  YAML file so someone can customize the report without touching code.

## How AI helped

I used Claude to scaffold this project. Specifically:
- Looked up OpenAQ's actual v3 API shape (auth header, pagination
  parameters, response schema) since I didn't have it memorized, and
  used that to write `fetch_data.py` correctly against the real API rather
  than guessing.
- Wrote the initial cut of `transform.py`, `build_report.py` and the test
  fixtures, which I then reviewed against the data-quality issues
  documented in the table above.
- The specific data-cleaning decisions (what counts as a glitch, the
  75% coverage threshold, keeping-vs-dropping incomplete days) and the
  report's target audience and framing were mine — the table in this
  README documents that reasoning rather than just describing what the
  code does, so a reviewer can evaluate the judgment calls, not just the
  syntax.
