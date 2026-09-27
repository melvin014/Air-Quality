"""
Pulls raw data from the OpenAQ v3 API and saves it to disk untouched.

Design choices, and why:

- Raw responses are saved exactly as OpenAQ returns them (one JSON file per
  API call), before any cleaning happens. If a transform bug is found later,
  we can re-run transform.py against the original raw data without
  re-hitting the API. This is the classic "raw / bronze layer" idea.
- The API key is read from the environment (via .env + python-dotenv), never
  hardcoded, and .env is gitignored.
- Pagination: OpenAQ list endpoints take `page` and `limit` query params and
  return `meta.found` (total matching records). We loop pages until we've
  collected `found` records or a page comes back empty.
- Rate limiting: the free tier allows 60 requests/minute. We enforce a fixed
  minimum delay between requests and back off on HTTP 429 using the
  Retry-After header when present.

Run this file directly to fetch a fresh snapshot:
    python -m src.fetch_data
"""
from __future__ import annotations

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from src import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fetch_data")

load_dotenv()

_last_request_time = 0.0


class OpenAQError(RuntimeError):
    pass


def _get_api_key() -> str:
    key = os.environ.get("OPENAQ_API_KEY")
    if not key:
        raise OpenAQError(
            "OPENAQ_API_KEY is not set. Copy .env.example to .env and add your "
            "free key from https://explore.openaq.org"
        )
    return key


def _throttle() -> None:
    """Sleep just enough to respect the rate limit between requests."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    wait = config.MIN_SECONDS_BETWEEN_REQUESTS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.monotonic()


def _request(path: str, params: dict | None = None) -> dict:
    """A single authenticated, rate-limited, retried GET against OpenAQ."""
    url = f"{config.API_BASE}{path}"
    headers = {"X-API-Key": _get_api_key()}

    for attempt in range(1, config.MAX_RETRIES + 1):
        _throttle()
        resp = requests.get(url, headers=headers, params=params, timeout=30)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            log.warning("Rate limited (429). Waiting %ss (attempt %s/%s)",
                        retry_after, attempt, config.MAX_RETRIES)
            time.sleep(retry_after)
            continue

        if resp.status_code == 401:
            raise OpenAQError("401 Unauthorized - check OPENAQ_API_KEY in .env")

        if resp.status_code >= 500:
            wait = 2 ** attempt
            log.warning("Server error %s on %s, retrying in %ss", resp.status_code, url, wait)
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp.json()

    raise OpenAQError(f"Failed to fetch {url} after {config.MAX_RETRIES} attempts")


def _paginate(path: str, params: dict) -> list[dict]:
    """Loop through all pages of a list endpoint, returning the combined results."""
    all_results: list[dict] = []
    page = 1
    limit = params.get("limit", 100)

    while True:
        page_params = {**params, "page": page, "limit": limit}
        data = _request(path, page_params)
        results = data.get("results", [])
        all_results.extend(results)

        found = (data.get("meta") or {}).get("found")
        if not results:
            break
        if isinstance(found, int) and len(all_results) >= found:
            break
        if len(results) < limit:
            # short page with no reliable `found` count -> assume last page
            break
        page += 1

    return all_results


def _save_raw(subdir: str, name: str, payload) -> Path:
    out_dir = config.RAW_DIR / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{name}.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    return out_path


def find_location_for_city(name: str, iso: str, lat: float, lon: float, radius_m: int) -> dict | None:
    """Find the monitoring station nearest a city coordinate with the most
    relevant sensors. Saves the raw /locations response for that city."""
    params = {"coordinates": f"{lat},{lon}", "radius": radius_m, "limit": 50}
    results = _paginate("/locations", params)
    _save_raw("locations", f"{iso}_{name.replace(' ', '_')}", {"query": params, "results": results})

    if not results:
        log.warning("No monitoring locations found near %s", name)
        return None

    def relevant_sensor_count(loc: dict) -> int:
        sensor_params = {s.get("parameter", {}).get("name") for s in loc.get("sensors", [])}
        return len(sensor_params & config.PARAMETERS_OF_INTEREST)

    best = max(results, key=relevant_sensor_count)
    if relevant_sensor_count(best) == 0:
        log.warning("Nearest stations to %s have none of our target pollutants", name)
        return None
    return best


def fetch_daily_measurements(sensor_id: int, city: str, parameter: str) -> list[dict]:
    """Pull the last LOOKBACK_DAYS of daily-aggregated measurements for one sensor."""
    params = {"limit": 100}
    results = _paginate(f"/sensors/{sensor_id}/measurements/daily", params)
    _save_raw(
        "measurements",
        f"{city.replace(' ', '_')}_{parameter}_sensor{sensor_id}",
        {"sensor_id": sensor_id, "city": city, "parameter": parameter, "results": results},
    )
    return results


def run(cities=None) -> None:
    cities = cities or config.CITIES
    fetch_started_at = datetime.now(timezone.utc).isoformat()
    manifest = {"fetch_started_at": fetch_started_at, "cities": []}

    for name, iso, lat, lon, radius in cities:
        log.info("Finding station for %s, %s", name, iso)
        location = find_location_for_city(name, iso, lat, lon, radius)
        city_record = {"city": name, "country": iso, "location_found": bool(location)}

        if location:
            city_record["location_id"] = location.get("id")
            city_record["location_name"] = location.get("name")
            sensors_pulled = []
            for sensor in location.get("sensors", []):
                param_name = sensor.get("parameter", {}).get("name")
                if param_name not in config.PARAMETERS_OF_INTEREST:
                    continue
                log.info("  Fetching %s daily measurements (sensor %s)", param_name, sensor["id"])
                fetch_daily_measurements(sensor["id"], name, param_name)
                sensors_pulled.append(param_name)
            city_record["parameters_pulled"] = sensors_pulled

        manifest["cities"].append(city_record)

    manifest["fetch_finished_at"] = datetime.now(timezone.utc).isoformat()
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.RAW_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    log.info("Fetch complete. Manifest written to %s", config.RAW_DIR / "manifest.json")


if __name__ == "__main__":
    try:
        run()
    except OpenAQError as e:
        log.error(str(e))
        sys.exit(1)
