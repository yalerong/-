"""Orchestrator: scan workspace currencies, fetch history, backtest, forecast, export signals.

Run from project root:
    python -m forecast.pipeline
    python -m forecast.pipeline --pair USD --pair EUR --skip-fetch
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import data_fetch, monthly_backtest, prophet_forecast

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "fx_workspace.json"
SIGNALS_FILE = DATA_DIR / "forecast_signals.json"
BASE_CURRENCY = "CNY"


def scan_currencies() -> list[str]:
    if not STATE_FILE.exists():
        return []
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for collection in ("exposures", "hedges", "settlements"):
        for row in state.get(collection, []) or []:
            cur = (row.get("currency") or "").upper()
            if cur and cur != BASE_CURRENCY:
                seen.add(cur)
    return sorted(seen)


def classify_tier(mape: float | None, direction_accuracy: float | None) -> str:
    if mape is None or direction_accuracy is None:
        return "reject"
    if mape <= 0.025 and direction_accuracy >= 0.55:
        return "support"
    if mape <= 0.05 and direction_accuracy >= 0.50:
        return "caution"
    return "reject"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(
    currencies: list[str] | None = None,
    horizon: int = 6,
    years: int = 6,
    skip_fetch: bool = False,
    access_key: str | None = None,
) -> dict:
    if currencies is None:
        currencies = scan_currencies()
    if not currencies:
        print("[pipeline] No foreign currencies discovered; nothing to forecast.")
        result = {"generated_at": _now_iso(), "horizon_months": horizon, "base_currency": BASE_CURRENCY, "signals": {}}
        SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SIGNALS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    signals: dict[str, dict] = {}
    for foreign in currencies:
        pair = f"{foreign}{BASE_CURRENCY}"
        print(f"[pipeline] === {pair} ===")
        try:
            if not skip_fetch:
                data_fetch.fetch_pair(foreign, BASE_CURRENCY, years=years, access_key=access_key)
            bt = monthly_backtest.backtest_pair(pair)
            fc = prophet_forecast.forecast_pair(pair, horizon_months=horizon)
        except Exception as exc:
            print(f"[pipeline] {pair} skipped: {exc}")
            continue
        tier = classify_tier(bt["mape"], bt["direction_accuracy"])
        signals[foreign] = {
            "pair": pair,
            "current": fc["last_actual"],
            "current_month": fc["last_actual_month"],
            "forecast": fc["forecast"],
            "direction": fc["overall_direction"],
            "mape": bt["mape"],
            "direction_accuracy": bt["direction_accuracy"],
            "n_test": bt["n_test"],
            "tier": tier,
        }
        print(f"[pipeline] {pair} mape={bt['mape']} dirAcc={bt['direction_accuracy']} tier={tier}")

    result = {
        "generated_at": _now_iso(),
        "horizon_months": horizon,
        "base_currency": BASE_CURRENCY,
        "signals": signals,
    }
    SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNALS_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pipeline] wrote {SIGNALS_FILE}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", action="append", help="Foreign currency code (repeatable, e.g. --pair USD --pair EUR)")
    parser.add_argument("--horizon", type=int, default=6)
    parser.add_argument("--years", type=int, default=6)
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse existing data/rates_history/*.csv")
    parser.add_argument("--access-key", default=os.environ.get("EXCHANGERATE_HOST_KEY"))
    args = parser.parse_args()
    run(
        currencies=args.pair,
        horizon=args.horizon,
        years=args.years,
        skip_fetch=args.skip_fetch,
        access_key=args.access_key,
    )


if __name__ == "__main__":
    main()
