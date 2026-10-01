#!/usr/bin/env bash
# Runs the full pipeline end to end: fetch -> transform -> report.
# From a clean clone: pip install -r requirements.txt, set up .env, then run this.
set -euo pipefail

echo "== 1/3 Fetching raw data from OpenAQ =="
python3 -m src.fetch_data

echo "== 2/3 Transforming into DuckDB =="
python3 -m src.transform

echo "== 3/3 Building HTML report =="
python3 -m src.build_report

echo "Done. Open report/index.html in a browser."
