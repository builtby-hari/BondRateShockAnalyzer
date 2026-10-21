#!/usr/bin/env python3
"""Fetch the latest U.S. Treasury par curve from FRED and write data/treasury_par_curve.csv.

Uses FRED's public CSV endpoint (no API key). Takes the most recent date on which all nine
constant-maturity tenors have a value. Yields are written as decimals (0.0477 = 4.77%).

Usage: python3 fetch_curve.py
"""

import csv
import datetime as dt
import io
import os
import time
import urllib.request

TENOR_SERIES = [(0.5, "DGS6MO"), (1, "DGS1"), (2, "DGS2"), (3, "DGS3"), (5, "DGS5"),
                (7, "DGS7"), (10, "DGS10"), (20, "DGS20"), (30, "DGS30")]
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd={start}"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "treasury_par_curve.csv")


RETRY_DELAYS = (2, 4, 4)  # seconds before each of the 3 retries


def fetch(series_id, start):
    url = URL.format(id=series_id, start=start)
    # Default urllib User-Agent on purpose: FRED stalls requests with custom User-Agents.
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except OSError:
            if attempt == len(RETRY_DELAYS):
                raise
            time.sleep(RETRY_DELAYS[attempt])


def parse(text):
    out = {}
    rows = csv.reader(io.StringIO(text))
    next(rows)
    for d, v in rows:
        if v.strip() not in ("", "."):
            out[d] = float(v)
    return out


def main():
    start = (dt.date.today() - dt.timedelta(days=21)).isoformat()
    tables = {tenor: parse(fetch(sid, start)) for tenor, sid in TENOR_SERIES}
    common = set.intersection(*(set(t) for t in tables.values()))
    if not common:
        raise SystemExit("no date with all nine tenors in the last 21 days")
    as_of = max(common)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["as_of", "tenor_years", "par_yield"])
        for tenor, _ in TENOR_SERIES:
            w.writerow([as_of, tenor, f"{tables[tenor][as_of] / 100:.5f}"])
    print(f"Wrote {OUT} as of {as_of}")


if __name__ == "__main__":
    main()
