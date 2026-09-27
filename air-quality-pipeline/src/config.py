"""
Shared configuration for the air quality pipeline.

Keeping this in one place means fetch_data.py, transform.py and
build_report.py all agree on which cities, parameters and paths are in play.
"""
from pathlib import Path

# --- Paths -------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT_DIR / "data" / "raw"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
REPORT_DIR = ROOT_DIR / "report"
DUCKDB_PATH = PROCESSED_DIR / "air_quality.duckdb"

# --- API -----------------------------------------------------------------
API_BASE = "https://api.openaq.org/v3"
# OpenAQ's stated free-tier limit is 60 requests/minute. We stay comfortably
# under that with a fixed delay between requests rather than trying to be
# clever about it.
MIN_SECONDS_BETWEEN_REQUESTS = 1.1
MAX_RETRIES = 5

# --- Cities of interest --------------------------------------------------
# (city, country_iso, lat, lon, search_radius_m)
# Radius is how far from the coordinate we'll search for a monitoring
# station. Some cities have dense networks (small radius is enough); others
# have sparse coverage and need a wider net.
CITIES = [
    ("London", "GB", 51.5072, -0.1276, 10_000),
    ("Paris", "FR", 48.8566, 2.3522, 10_000),
    ("Berlin", "DE", 52.5200, 13.4050, 10_000),
    ("New York", "US", 40.7128, -74.0060, 10_000),
    ("Los Angeles", "US", 34.0522, -118.2437, 15_000),
    ("Delhi", "IN", 28.6139, 77.2090, 15_000),
    ("Beijing", "CN", 39.9042, 116.4074, 15_000),
    ("Sao Paulo", "BR", -23.5505, -46.6333, 15_000),
    ("Lagos", "NG", 6.5244, 3.3792, 20_000),
    ("Sydney", "AU", -33.8688, 151.2093, 15_000),
]

# Pollutants we care about for this report. OpenAQ calls these "parameters";
# names must match the API's parameter `name` field.
PARAMETERS_OF_INTEREST = {"pm25", "pm10", "no2", "o3"}

# How many days of daily-aggregated history to pull per sensor per run.
LOOKBACK_DAYS = 30

# WHO 2021 Air Quality Guideline annual mean thresholds (ug/m3), used in the
# report to give readers a reference point rather than a bare number.
WHO_GUIDELINE_UGM3 = {
    "pm25": 5.0,
    "pm10": 15.0,
    "no2": 10.0,
    "o3": 60.0,  # peak season average, used loosely here as a rough marker
}
