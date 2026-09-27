"""
Builds a static HTML report from the cleaned DuckDB data.

Audience: someone deciding where air is currently breathable - e.g. a
traveler picking between cities, or someone with asthma/allergies checking
whether today's a bad-air day where they live. That audience wants:
  - a quick "is this city currently OK" read, not raw numbers
  - a reference point (WHO guideline), not just a bar chart in a vacuum
  - honesty about gaps in the data, since air quality reporting is patchy
    in a lot of the world and pretending otherwise would be misleading

Charts are rendered with matplotlib and embedded as base64 PNGs so the
report is a single self-contained HTML file with no external dependencies -
it opens the same way on any machine, no server needed.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build_report")

PARAM_LABELS = {"pm25": "PM2.5", "pm10": "PM10", "no2": "NO\u2082", "o3": "O\u2083"}


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _query(con, sql, params=None) -> pd.DataFrame:
    return con.execute(sql, params or []).fetchdf()


def make_city_comparison_chart(con, parameter: str) -> str | None:
    df = _query(con, """
        SELECT city, AVG(value_ugm3) AS avg_value
        FROM fact_daily_air_quality
        WHERE parameter = ? AND is_complete AND value_ugm3 IS NOT NULL
        GROUP BY city
        ORDER BY avg_value DESC
    """, [parameter])
    if df.empty:
        return None

    guideline = config.WHO_GUIDELINE_UGM3.get(parameter)
    fig, ax = plt.subplots(figsize=(7, max(2.5, 0.4 * len(df))))
    colors = ["#d9534f" if v > (guideline or float("inf")) else "#5cb85c" for v in df["avg_value"]]
    ax.barh(df["city"], df["avg_value"], color=colors)
    if guideline:
        ax.axvline(guideline, color="black", linestyle="--", linewidth=1)
        ax.text(guideline, len(df) - 0.5, f" WHO guideline ({guideline})",
                fontsize=8, va="top")
    ax.set_xlabel(f"{PARAM_LABELS.get(parameter, parameter)} (\u00b5g/m\u00b3), period average")
    ax.invert_yaxis()
    fig.tight_layout()
    return _fig_to_base64(fig)


def make_trend_chart(con, parameter: str) -> str | None:
    df = _query(con, """
        SELECT city, date, value_ugm3
        FROM fact_daily_air_quality
        WHERE parameter = ? AND is_complete AND value_ugm3 IS NOT NULL
        ORDER BY city, date
    """, [parameter])
    if df.empty:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    for city, group in df.groupby("city"):
        ax.plot(pd.to_datetime(group["date"]), group["value_ugm3"], marker="o", markersize=3, label=city)
    ax.set_ylabel(f"{PARAM_LABELS.get(parameter, parameter)} (\u00b5g/m\u00b3)")
    ax.legend(fontsize=8, loc="upper left", ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    return _fig_to_base64(fig)


def coverage_summary(con) -> pd.DataFrame:
    return _query(con, """
        SELECT
            city,
            parameter,
            COUNT(*) AS days_fetched,
            SUM(CASE WHEN is_complete THEN 1 ELSE 0 END) AS days_usable,
            ROUND(100.0 * SUM(CASE WHEN is_complete THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_usable
        FROM fact_daily_air_quality
        GROUP BY city, parameter
        ORDER BY city, parameter
    """)


def render_html(con) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    params_present = [r[0] for r in con.execute(
        "SELECT DISTINCT parameter FROM fact_daily_air_quality"
    ).fetchall()]

    sections = []
    for param in [p for p in ["pm25", "pm10", "no2", "o3"] if p in params_present]:
        cmp_chart = make_city_comparison_chart(con, param)
        trend_chart = make_trend_chart(con, param)
        if not cmp_chart:
            continue
        sections.append(f"""
        <section class="param-section">
          <h2>{PARAM_LABELS.get(param, param)}</h2>
          <div class="charts">
            <div class="chart-card">
              <h3>City comparison (period average)</h3>
              <img src="data:image/png;base64,{cmp_chart}" alt="{param} city comparison chart">
            </div>
            {"<div class='chart-card'><h3>Daily trend</h3><img src='data:image/png;base64," + trend_chart + "' alt='" + param + " trend chart'></div>" if trend_chart else ""}
          </div>
        </section>""")

    coverage_df = coverage_summary(con)
    coverage_rows = "\n".join(
        f"<tr><td>{r.city}</td><td>{PARAM_LABELS.get(r.parameter, r.parameter)}</td>"
        f"<td>{r.days_fetched}</td><td>{r.days_usable}</td><td>{r.pct_usable}%</td></tr>"
        for r in coverage_df.itertuples()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Global Air Quality Snapshot</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 900px;
         margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.5; }}
  h1 {{ margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 0.25rem; }}
  .charts {{ display: flex; flex-wrap: wrap; gap: 1.5rem; }}
  .chart-card {{ flex: 1 1 380px; }}
  .chart-card img {{ max-width: 100%; border: 1px solid #eee; border-radius: 6px; }}
  section.param-section {{ margin: 2.5rem 0; padding-top: 1rem; border-top: 1px solid #eee; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; }}
  th {{ background: #fafafa; }}
  footer {{ margin-top: 3rem; color: #888; font-size: 0.85rem; }}
  .note {{ background: #fff8e6; border-left: 3px solid #f0ad4e; padding: 0.75rem 1rem; margin: 1.5rem 0; }}
</style>
</head>
<body>
  <h1>Global Air Quality Snapshot</h1>
  <p class="subtitle">Generated {generated_at} &middot; source: OpenAQ v3 API</p>

  <p>This report compares recent outdoor air pollution across a set of world
  cities, for anyone deciding where the air is currently easiest to
  breathe &mdash; travelers, people with respiratory conditions, or anyone
  just curious. Dashed lines mark the WHO 2021 air quality guideline for
  that pollutant; bars past it are shown in red.</p>

  <div class="note">
    <strong>Data honesty note:</strong> monitoring coverage is uneven
    worldwide. A city missing from a chart below means no station near it
    reported that pollutant in this run &mdash; not that its air is clean.
    See the coverage table at the bottom for exactly how complete each
    city's data is.
  </div>

  {''.join(sections)}

  <section>
    <h2>Data completeness</h2>
    <table>
      <tr><th>City</th><th>Pollutant</th><th>Days fetched</th><th>Days usable</th><th>% usable</th></tr>
      {coverage_rows}
    </table>
    <p style="color:#666; font-size:0.85rem;">"Usable" excludes days where the
    sensor reported less than 75% of expected hourly readings, or an
    impossible (negative) value.</p>
  </section>

  <footer>
    Built with a Python + DuckDB pipeline against the OpenAQ v3 API.
    See the project README for methodology, limitations and how to
    reproduce this report.
  </footer>
</body>
</html>"""


def run(db_path=config.DUCKDB_PATH, out_path=None) -> None:
    out_path = out_path or (config.REPORT_DIR / "index.html")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        html = render_html(con)
    finally:
        con.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    log.info("Report written to %s", out_path)


if __name__ == "__main__":
    run()
