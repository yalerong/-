"""Fetch historical FX rates and persist month-end series.

Default source: exchangerate.host timeseries endpoint.
Set EXCHANGERATE_HOST_KEY env var (or --access-key) if the free tier requires it.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "rates_history"
USER_AGENT = "FX-Hedge-Lab/forecast"


def _chunk_by_year(start: date, end: date):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, date(cursor.year, 12, 31))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def fetch_daily(base: str, quote: str, start: date, end: date, access_key: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for chunk_start, chunk_end in _chunk_by_year(start, end):
        params = {
            "start_date": chunk_start.isoformat(),
            "end_date": chunk_end.isoformat(),
            "base": base,
            "symbols": quote,
        }
        if access_key:
            params["access_key"] = access_key
        url = f"https://api.exchangerate.host/timeseries?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("success") is False:
            raise RuntimeError(f"exchangerate.host error: {payload.get('error') or payload}")
        for d, row in (payload.get("rates") or {}).items():
            if isinstance(row, dict) and quote in row and row[quote] is not None:
                out[d] = float(row[quote])
    return out


def to_month_end(daily: dict[str, float]) -> list[tuple[str, float]]:
    by_month: dict[str, tuple[str, float]] = {}
    for d, v in daily.items():
        m = d[:7]
        prev = by_month.get(m)
        if prev is None or d > prev[0]:
            by_month[m] = (d, v)
    return sorted(((d, v) for d, v in by_month.values()), key=lambda r: r[0])


def write_history(pair: str, rows: list[tuple[str, float]]) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{pair}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ds", "y"])
        for ds, y in rows:
            w.writerow([ds, f"{y:.6f}"])
    return path


def fetch_pair(foreign: str, quote: str = "CNY", years: int = 6, access_key: str | None = None) -> Path:
    end = date.today()
    start = date(end.year - years, end.month, 1)
    daily = fetch_daily(foreign, quote, start, end, access_key)
    if not daily:
        raise RuntimeError(f"No data returned for {foreign}{quote}")
    monthly = to_month_end(daily)
    path = write_history(f"{foreign}{quote}", monthly)
    daily_rows = sorted(daily.items(), key=lambda r: r[0])
    write_history(f"{foreign}{quote}_daily", daily_rows)
    print(f"[fetch] {foreign}{quote}: {len(monthly)} month-end points ({len(daily_rows)} daily) -> {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--foreign", required=True, help="e.g. USD")
    parser.add_argument("--quote", default="CNY")
    parser.add_argument("--years", type=int, default=6)
    parser.add_argument("--access-key", default=os.environ.get("EXCHANGERATE_HOST_KEY"))
    args = parser.parse_args()
    fetch_pair(args.foreign, args.quote, args.years, args.access_key)


if __name__ == "__main__":
    main()
