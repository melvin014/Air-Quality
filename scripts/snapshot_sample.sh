#!/usr/bin/env bash
set -euo pipefail

if [ ! -f data/processed/air_quality.duckdb ]; then
  echo "No processed data found - run ./run_pipeline.sh first." >&2
  exit 1
fi

rm -rf data/sample
mkdir -p data/sample/raw data/sample/processed data/sample/report

cp -r data/raw/* data/sample/raw/ 2>/dev/null || true
cp data/processed/air_quality.duckdb data/sample/processed/
cp report/index.html data/sample/report/ 2>/dev/null || true

date -u +"%Y-%m-%dT%H:%M:%SZ" > data/sample/snapshot_captured_at.txt

echo "Snapshot written to data/sample/."