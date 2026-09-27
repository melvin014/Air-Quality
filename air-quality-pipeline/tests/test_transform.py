"""
Tests transform.py against fixture data that mirrors OpenAQ's real response
shape (captured from https://docs.openaq.org/api). No network access needed
or used here - that's the point of separating raw storage from transform.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import transform

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_locations_flattens_and_tags_city():
    df = transform.load_locations(FIXTURES)
    assert len(df) == 2
    london = df[df["city"] == "London"].iloc[0]
    assert london["location_id"] == 1001
    assert london["country_iso"] == "GB"


def test_load_measurements_drops_exact_duplicates():
    df = transform.load_measurements(FIXTURES)
    london_pm25 = df[(df.city == "London") & (df.parameter == "pm25")]
    # fixture has 4 raw rows but one is an exact duplicate -> 3 unique days
    assert len(london_pm25) == 3


def test_low_coverage_day_becomes_null_not_dropped():
    df = transform.load_measurements(FIXTURES)
    london_pm25 = df[(df.city == "London") & (df.parameter == "pm25")]
    low_coverage_row = london_pm25[london_pm25.coverage_pct < 75]
    assert len(low_coverage_row) == 1
    assert low_coverage_row.iloc[0]["value_ugm3"] is None or \
        (low_coverage_row.iloc[0]["value_ugm3"] != low_coverage_row.iloc[0]["value_ugm3"])  # NaN check
    assert low_coverage_row.iloc[0]["is_complete"] == False


def test_negative_sensor_glitch_becomes_null():
    df = transform.load_measurements(FIXTURES)
    delhi_pm25 = df[(df.city == "Delhi") & (df.parameter == "pm25")]
    glitch_row = delhi_pm25[delhi_pm25.value_ugm3.isna()]
    # the -9999 row should be nulled out, not kept as a real reading
    assert len(glitch_row) == 1


def test_ozone_ppm_converted_to_ugm3():
    df = transform.load_measurements(FIXTURES)
    delhi_o3 = df[(df.city == "Delhi") & (df.parameter == "o3")]
    assert len(delhi_o3) == 1
    value = delhi_o3.iloc[0]["value_ugm3"]
    # 0.021 ppm * 1960 = 41.16
    assert abs(value - 41.16) < 0.01


def test_duckdb_load_is_idempotent_on_rerun(tmp_path):
    locations_df = transform.load_locations(FIXTURES)
    measurements_df = transform.load_measurements(FIXTURES)
    db_path = tmp_path / "test.duckdb"

    transform.load_to_duckdb(locations_df, measurements_df, db_path)
    transform.load_to_duckdb(locations_df, measurements_df, db_path)  # rerun

    import duckdb
    con = duckdb.connect(str(db_path))
    count = con.execute("SELECT COUNT(*) FROM fact_daily_air_quality").fetchone()[0]
    con.close()
    # same input loaded twice should not double the row count
    assert count == len(measurements_df)
