"""
Turns raw OpenAQ JSON into a clean, consistent DuckDB table.

Engineering decisions made here, and why:

1. Types: `value` arrives as a float or JSON null. We coerce to float64 and
   keep nulls as real NULLs (not 0, not -9999) so aggregates don't silently
   lie.
2. Nulls / sentinel values: some sensors report physically impossible values
   (e.g. negative concentrations) when hardware glitches. We treat any
   negative reading, and any day with <75% expected hourly coverage, as
   missing rather than trusting the number.
3. Units: OpenAQ reports whichever unit the original provider used. Ozone
   sometimes arrives in ppm instead of ug/m3. We convert ppm -> ug/m3 for O3
   using the standard conversion (1 ppm O3 ~ 1960 ug/m3 at reference
   conditions) so every row in the fact table is comparable.
4. Duplicates: the API can return the same reading twice across paginated
   requests (seen in practice near page boundaries). We dedupe on
   (sensor_id, date) keeping one row.
5. Naming: raw JSON keys are camelCase and deeply nested; the processed
   table uses flat snake_case columns.
6. Repeat runs: transform is idempotent. Loading is done as
   "delete rows for any (city, parameter) we're about to reload, then
   insert" inside one transaction, so running the pipeline twice in a row
   (or after a partial failure) does not create duplicate rows.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import duckdb
import pandas as pd

from src import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("transform")

MIN_COVERAGE_PCT = 75.0
PPM_TO_UGM3_O3 = 1960.0  # standard conversion factor for ozone at 25C/1atm


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_locations(raw_dir: Path) -> pd.DataFrame:
    """Flatten every locations/*.json fixture/response into one dim table."""
    rows = []
    loc_dir = raw_dir / "locations"
    if not loc_dir.exists():
        return pd.DataFrame(columns=["location_id", "location_name", "city", "country_iso"])

    for path in sorted(loc_dir.glob("*.json")):
        payload = _load_json(path)
        # filename convention is {ISO}_{City_With_Underscores}
        iso, _, city_slug = path.stem.partition("_")
        city = city_slug.replace("_", " ")
        for loc in payload.get("results", []):
            rows.append({
                "location_id": loc.get("id"),
                "location_name": loc.get("name"),
                "city": city,
                "country_iso": iso,
            })
    df = pd.DataFrame(rows)
    return df.drop_duplicates(subset=["location_id"])


def _normalize_value(value, parameter: str, units: str):
    if value is None:
        return None
    if value < 0:
        return None  # physically impossible; treat as sensor error
    if parameter == "o3" and units == "ppm":
        return value * PPM_TO_UGM3_O3
    return value


def load_measurements(raw_dir: Path) -> pd.DataFrame:
    """Flatten every measurements/*.json fixture/response into one fact table."""
    rows = []
    meas_dir = raw_dir / "measurements"
    if not meas_dir.exists():
        return pd.DataFrame(columns=[
            "sensor_id", "city", "parameter", "date", "value_ugm3",
            "coverage_pct", "is_complete",
        ])

    for path in sorted(meas_dir.glob("*.json")):
        payload = _load_json(path)
        city = payload.get("city")
        parameter = payload.get("parameter")
        sensor_id = payload.get("sensor_id")

        for r in payload.get("results", []):
            units = r.get("parameter", {}).get("units")
            raw_value = r.get("value")
            coverage = (r.get("coverage") or {}).get("percentComplete")
            date_str = r.get("period", {}).get("datetimeFrom", {}).get("utc", "")
            date = date_str[:10] if date_str else None

            is_complete = coverage is not None and coverage >= MIN_COVERAGE_PCT
            value = _normalize_value(raw_value, parameter, units)
            if not is_complete:
                value = None  # don't trust a partial-day average

            rows.append({
                "sensor_id": sensor_id,
                "city": city,
                "parameter": parameter,
                "date": date,
                "value_ugm3": value,
                "coverage_pct": coverage,
                "is_complete": is_complete,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df = df.dropna(subset=["date", "sensor_id"])
    df["value_ugm3"] = pd.to_numeric(df["value_ugm3"], errors="coerce")

    # Dedupe: same sensor + date can appear twice across paginated calls.
    before = len(df)
    df = df.drop_duplicates(subset=["sensor_id", "date"], keep="first")
    if before != len(df):
        log.info("Dropped %d duplicate sensor/date rows", before - len(df))

    return df.reset_index(drop=True)


def load_to_duckdb(locations_df: pd.DataFrame, measurements_df: pd.DataFrame,
                    db_path: Path = config.DUCKDB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS dim_locations (
                location_id BIGINT PRIMARY KEY,
                location_name VARCHAR,
                city VARCHAR,
                country_iso VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS fact_daily_air_quality (
                sensor_id BIGINT,
                city VARCHAR,
                parameter VARCHAR,
                date DATE,
                value_ugm3 DOUBLE,
                coverage_pct DOUBLE,
                is_complete BOOLEAN,
                PRIMARY KEY (sensor_id, date)
            )
        """)

        con.execute("BEGIN TRANSACTION")

        # Idempotent load for dim_locations: replace wholesale, it's small.
        con.execute("DELETE FROM dim_locations")
        con.register("locations_df", locations_df)
        con.execute("INSERT INTO dim_locations SELECT * FROM locations_df")

        # Idempotent load for facts: delete rows for any city we're about to
        # reload (covers reruns / re-fetches), then insert fresh.
        con.register("measurements_df", measurements_df)
        cities_in_batch = measurements_df["city"].dropna().unique().tolist() if not measurements_df.empty else []
        if cities_in_batch:
            con.execute(
                "DELETE FROM fact_daily_air_quality WHERE city = ANY(?)",
                [cities_in_batch],
            )
            con.execute("INSERT INTO fact_daily_air_quality SELECT * FROM measurements_df")

        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def run(raw_dir: Path = config.RAW_DIR, db_path: Path = config.DUCKDB_PATH) -> None:
    log.info("Loading raw locations from %s", raw_dir / "locations")
    locations_df = load_locations(raw_dir)
    log.info("Loading raw measurements from %s", raw_dir / "measurements")
    measurements_df = load_measurements(raw_dir)

    log.info("%d locations, %d measurement rows (%d usable after quality checks)",
              len(locations_df), len(measurements_df),
              int(measurements_df["is_complete"].sum()) if not measurements_df.empty else 0)

    load_to_duckdb(locations_df, measurements_df, db_path)
    log.info("Loaded into %s", db_path)


if __name__ == "__main__":
    run()
