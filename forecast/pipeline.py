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

import math

from . import data_fetch, monthly_backtest, prophet_forecast, trend_gate

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


# support 档的附加约束：样本量、方向准确率显著性、区间覆盖率
SUPPORT_MIN_N = 24
DIRECTION_P_MAX = 0.10
# 名义区间 80%：覆盖率低于 0.70 说明模型低估不确定性，低于 0.40 说明区间基本失真
SUPPORT_MIN_COVERAGE = 0.70
CAUTION_MIN_COVERAGE = 0.40


def binom_p_one_sided(hits: int, n: int) -> float:
    """H0: 方向命中率=0.5 时，命中 >= hits 的概率（单侧精确二项检验）。"""
    if n <= 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(hits, n + 1)) / 2**n


def _demote(tier: str) -> str:
    return {"support": "caution", "caution": "reject"}.get(tier, "reject")


def classify_tier(bt: dict) -> tuple[str, list[str]]:
    """由回测指标定档，返回 (tier, 降档原因列表)。"""
    reasons: list[str] = []
    mape = bt.get("mape")
    direction_accuracy = bt.get("direction_accuracy")
    if mape is None or direction_accuracy is None:
        return "reject", ["回测指标缺失"]
    if mape <= 0.025 and direction_accuracy >= 0.55:
        tier = "support"
    elif mape <= 0.05 and direction_accuracy >= 0.50:
        tier = "caution"
    else:
        return "reject", []

    n_test = bt.get("n_test") or 0
    direction_hits = bt.get("direction_hits")
    if tier == "support":
        if n_test < SUPPORT_MIN_N:
            tier = "caution"
            reasons.append(f"回测样本不足（{n_test} < {SUPPORT_MIN_N}），降为 caution")
        elif direction_hits is not None:
            p = binom_p_one_sided(int(direction_hits), int(n_test))
            if p > DIRECTION_P_MAX:
                tier = "caution"
                reasons.append(f"方向准确率与抛硬币无显著差异（p={p:.2f}），降为 caution")

    coverage = bt.get("interval_coverage")
    if coverage is None:
        if tier == "support":
            tier = "caution"
            reasons.append("缺少区间覆盖率，降为 caution")
    else:
        if tier == "support" and coverage < SUPPORT_MIN_COVERAGE:
            tier = "caution"
            reasons.append(f"区间覆盖率 {coverage:.0%} 低于 {SUPPORT_MIN_COVERAGE:.0%}，降为 caution")
        if tier == "caution" and coverage < CAUTION_MIN_COVERAGE:
            tier = "reject"
            reasons.append(f"区间覆盖率 {coverage:.0%} 低于 {CAUTION_MIN_COVERAGE:.0%}，预测区间失真，降为 reject")
    return tier, reasons


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
        tier, tier_reasons = classify_tier(bt)

        gate = None
        try:
            gate = trend_gate.evaluate_pair(pair)
        except Exception as exc:
            print(f"[pipeline] {pair} trend gate unavailable: {exc}")
        if gate and tier != "reject" and gate["direction"] in ("up", "down"):
            conflicts = gate["direction"] != fc["overall_direction"]
            if conflicts and gate["alignment"] >= trend_gate.STRONG_ALIGNMENT:
                tier = _demote(tier)
                tier_reasons.append(
                    f"GMMA 强趋势（{gate['direction']} {gate['alignment']}/6）与预测方向相反，降一档"
                )

        signals[foreign] = {
            "pair": pair,
            "current": fc["last_actual"],
            "current_month": fc["last_actual_month"],
            "forecast": fc["forecast"],
            "direction": fc["overall_direction"],
            "mape": bt["mape"],
            "direction_accuracy": bt["direction_accuracy"],
            "n_test": bt["n_test"],
            "interval_coverage": bt.get("interval_coverage"),
            "trend": gate,
            "tier": tier,
            "tier_reasons": tier_reasons,
        }
        print(
            f"[pipeline] {pair} mape={bt['mape']} dirAcc={bt['direction_accuracy']} "
            f"coverage={bt.get('interval_coverage')} trend={gate and gate['direction']} tier={tier}"
        )

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
