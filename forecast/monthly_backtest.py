"""Rolling monthly backtest: train up to month M, predict M+1, compare to actual."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "rates_history"
BACKTEST_DIR = ROOT / "data" / "backtest"
# Prophet 预测区间的名义置信度；interval_coverage 应接近该值才说明区间可信
NOMINAL_INTERVAL = 0.8


def _load_deps():
    try:
        from prophet import Prophet  # type: ignore
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Prophet not installed. Run: pip install prophet pandas") from exc
    return Prophet, pd


def backtest_pair(pair: str, min_train: int = 30, step_horizon: int = 1) -> dict:
    Prophet, pd = _load_deps()
    path = HISTORY_DIR / f"{pair}.csv"
    if not path.exists():
        raise FileNotFoundError(f"History not found: {path}. Run data_fetch first.")
    df = pd.read_csv(path)
    df["ds"] = pd.to_datetime(df["ds"])

    n = len(df)
    if n < min_train + step_horizon + 6:
        raise RuntimeError(f"Not enough history for backtest: {n} rows (need >= {min_train + step_horizon + 6})")

    abs_pct_errors: list[float] = []
    direction_hits = 0
    direction_total = 0
    coverage_hits = 0
    coverage_total = 0

    for i in range(min_train, n - step_horizon):
        train = df.iloc[: i + 1]
        actual_row = df.iloc[i + step_horizon]
        m = Prophet(
            weekly_seasonality=False,
            daily_seasonality=False,
            yearly_seasonality=True,
            changepoint_prior_scale=0.05,
            interval_width=NOMINAL_INTERVAL,
        )
        m.fit(train)
        future = m.make_future_dataframe(periods=step_horizon, freq="ME", include_history=False)
        fc = m.predict(future)
        pred = float(fc["yhat"].iloc[-1])
        lower = float(fc["yhat_lower"].iloc[-1])
        upper = float(fc["yhat_upper"].iloc[-1])
        actual = float(actual_row["y"])
        prev_actual = float(train["y"].iloc[-1])
        if actual != 0:
            abs_pct_errors.append(abs(pred - actual) / abs(actual))
        direction_hits += int((pred > prev_actual) == (actual > prev_actual))
        direction_total += 1
        coverage_hits += int(lower <= actual <= upper)
        coverage_total += 1

    mape = sum(abs_pct_errors) / len(abs_pct_errors) if abs_pct_errors else None
    direction_accuracy = direction_hits / direction_total if direction_total else None
    interval_coverage = coverage_hits / coverage_total if coverage_total else None

    out = {
        "pair": pair,
        "min_train": min_train,
        "step_horizon": step_horizon,
        "n_test": direction_total,
        "mape": round(mape, 4) if mape is not None else None,
        "direction_accuracy": round(direction_accuracy, 4) if direction_accuracy is not None else None,
        "direction_hits": direction_hits,
        "interval_coverage": round(interval_coverage, 4) if interval_coverage is not None else None,
        "nominal_interval": NOMINAL_INTERVAL,
    }
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    (BACKTEST_DIR / f"{pair}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", required=True, help="e.g. USDCNY")
    parser.add_argument("--min-train", type=int, default=30)
    parser.add_argument("--horizon", type=int, default=1)
    args = parser.parse_args()
    result = backtest_pair(args.pair, args.min_train, args.horizon)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
