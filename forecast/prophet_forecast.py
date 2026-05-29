"""Fit Prophet on month-end history and emit next-N-month forecast."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "rates_history"
FORECAST_DIR = ROOT / "data" / "forecasts"


def _load_deps():
    try:
        from prophet import Prophet  # type: ignore
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Prophet not installed. Run: pip install prophet pandas") from exc
    return Prophet, pd


def forecast_pair(pair: str, horizon_months: int = 6) -> dict:
    Prophet, pd = _load_deps()
    path = HISTORY_DIR / f"{pair}.csv"
    if not path.exists():
        raise FileNotFoundError(f"History not found: {path}. Run data_fetch first.")
    df = pd.read_csv(path)
    df["ds"] = pd.to_datetime(df["ds"])

    last_actual = float(df["y"].iloc[-1])
    last_month = df["ds"].iloc[-1].strftime("%Y-%m")

    model = Prophet(
        weekly_seasonality=False,
        daily_seasonality=False,
        yearly_seasonality=True,
        changepoint_prior_scale=0.05,
    )
    model.fit(df)
    future = model.make_future_dataframe(periods=horizon_months, freq="ME", include_history=False)
    fc = model.predict(future)

    rows: list[dict] = []
    prev = last_actual
    for _, r in fc.iterrows():
        yhat = float(r["yhat"])
        rows.append({
            "month": r["ds"].strftime("%Y-%m"),
            "rate": round(yhat, 6),
            "dir": "up" if yhat > prev else ("down" if yhat < prev else "flat"),
        })
        prev = yhat

    final_rate = rows[-1]["rate"] if rows else last_actual
    overall = "up" if final_rate > last_actual else ("down" if final_rate < last_actual else "flat")

    FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = FORECAST_DIR / f"{pair}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "yhat", "dir"])
        for r in rows:
            w.writerow([r["month"], r["rate"], r["dir"]])

    return {
        "pair": pair,
        "last_actual": round(last_actual, 6),
        "last_actual_month": last_month,
        "horizon_months": horizon_months,
        "forecast": rows,
        "overall_direction": overall,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", required=True, help="e.g. USDCNY")
    parser.add_argument("--horizon", type=int, default=6)
    args = parser.parse_args()
    result = forecast_pair(args.pair, args.horizon)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
