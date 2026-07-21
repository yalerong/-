"""GMMA 势能趋势 gate：用日线价格结构判断趋势方向与一致度。

方法来自公开的 GMMA 势能地形思路：
- 六组长短 EMA 配对差值 Dk = EMA_slow - EMA_fast
- 方向由第一层 (15,30) 符号决定：D1 < 0（快线在上）为 up
- 一致度 alignment = 与第一层同号的层数（0~6）
- 势能 E = -sum(W * Dk / ATR)，ATR 用 close-only 代理（|ΔClose| 的滚动均值）

该 gate 不产生买卖信号，只回答"预测方向与价格结构是否共振"：
预测方向与强趋势（alignment >= STRONG_ALIGNMENT）相反时，pipeline 将信号降一档。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "data" / "rates_history"

SHORT_PERIODS = [3, 5, 8, 10, 12, 15]
LONG_PERIODS = [30, 35, 40, 45, 50, 60]
PAIRS = [(15, 30), (12, 35), (10, 40), (8, 45), (5, 50), (3, 60)]
WEIGHTS = [0.35, 0.25, 0.18, 0.12, 0.07, 0.03]
ATR_PERIOD = 14
ENERGY_SMOOTH = 5
# 视为"强趋势"的最低层级一致度，用于对冲信号的降档判断
STRONG_ALIGNMENT = 5
MIN_ROWS = max(LONG_PERIODS) + ATR_PERIOD


def _load_pd():
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pandas not installed. Run: pip install pandas") from exc
    return pd


def evaluate_series(closes: list[float]) -> dict:
    """对一条日线收盘序列计算最新一根 bar 的趋势 gate。"""
    pd = _load_pd()
    if len(closes) < MIN_ROWS:
        raise RuntimeError(f"Not enough daily rows for trend gate: {len(closes)} (need >= {MIN_ROWS})")
    s = pd.Series(closes, dtype="float64")

    diffs = []
    for fast, slow in PAIRS:
        ema_fast = s.ewm(span=fast, adjust=False).mean()
        ema_slow = s.ewm(span=slow, adjust=False).mean()
        diffs.append(ema_slow - ema_fast)

    # close-only ATR 代理：没有 High/Low 时退化为收盘价变动幅度的滚动均值
    atr = s.diff().abs().rolling(ATR_PERIOD).mean()

    energy = None
    for w, d in zip(WEIGHTS, diffs):
        term = -w * (d / atr)
        energy = term if energy is None else energy + term
    energy = energy.rolling(ENERGY_SMOOTH).mean()

    last_diffs = [float(d.iloc[-1]) for d in diffs]
    d1_last = last_diffs[0]
    direction = "up" if d1_last < 0 else ("down" if d1_last > 0 else "flat")
    if d1_last == 0:
        alignment = 0
    else:
        alignment = sum(1 for v in last_diffs if v != 0 and (v < 0) == (d1_last < 0))

    # ATR 为零（钉住汇率、数据源节假日回填）时 energy 会是 NaN/inf，
    # 落进 JSON 会写出非法的 NaN 字面量，必须转成 None
    e_last = float(energy.iloc[-1])
    e_out = round(e_last, 4) if math.isfinite(e_last) else None
    return {
        "direction": direction,
        "alignment": alignment,
        "energy": e_out,
        "n_days": len(closes),
    }


def evaluate_pair(pair: str) -> dict:
    """读取 data_fetch 落盘的日线 CSV 并计算趋势 gate。"""
    pd = _load_pd()
    path = HISTORY_DIR / f"{pair}_daily.csv"
    if not path.exists():
        raise FileNotFoundError(f"Daily history not found: {path}. Run data_fetch first.")
    df = pd.read_csv(path)
    out = evaluate_series(df["y"].astype(float).tolist())
    out["pair"] = pair
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", required=True, help="e.g. USDCNY")
    args = parser.parse_args()
    print(json.dumps(evaluate_pair(args.pair), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
