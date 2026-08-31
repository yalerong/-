"""事件研究：方向命中率、安慰剂、增量检验、波动目标、长窗状态变量。

规则全部来自 docs/event-driven-plan.md，本模块不做任何参数搜索、不试第二组阈值。

    python -m events.event_study
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from . import market_fetch
from .calendar import QuoteCalendar

ROOT = Path(__file__).resolve().parent.parent
EVENTS_CSV = ROOT / "data" / "events" / "events.csv"
REPORT = ROOT / "data" / "events" / "study_report.json"

# ---- 冻结常量（docs/event-driven-plan.md 第 5、8 节） ----
SURPRISE_D2, SURPRISE_DXY = 0.03, 0.002          # 主档：3bp / 0.20%
STRICT_D2, STRICT_DXY = 0.10, 0.005              # 严档：10bp / 0.50%
TRAIN_START, TRAIN_END = "2019-06-28", "2024-12-31"
OOS_START, OOS_END = "2025-01-01", "2026-08-21"
SEED = 20260830
N_PLACEBO = 1000
TREND_LOOKBACK = 20
LONG_LOOKBACK = 60

US_CATS = {"US_MON", "US_DATA"}
CN_CATS = {"CN_POL", "CN_DATA"}


def binom_p_one_sided(hits: int, n: int) -> float:
    """H0: 命中率=0.5 时，命中 >= hits 的概率（单侧精确二项检验）。"""
    if n <= 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(hits, n + 1)) / 2**n


def two_prop_p(h1: int, n1: int, h2: int, n2: int) -> float:
    """两样本比例差的单侧正态检验，H1 是 p1 > p2。"""
    if n1 == 0 or n2 == 0:
        return 1.0
    p1, p2 = h1 / n1, h2 / n2
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return 0.5 * math.erfc(z / math.sqrt(2))


# ---------------------------------------------------------------- 特征


def build_features() -> tuple[QuoteCalendar, dict]:
    cal = QuoteCalendar.from_series("DEXCHUS")
    px = {date.fromisoformat(d): v for d, v in market_fetch.load_series("DEXCHUS")}
    d2 = {date.fromisoformat(d): v for d, v in market_fetch.load_series("DGS2")}
    dxy = {date.fromisoformat(d): v for d, v in market_fetch.load_series("DTWEXBGS")}
    vix = {date.fromisoformat(d): v for d, v in market_fetch.load_series("VIXCLS")}

    feats: dict = {}
    for day in cal.dates:
        prev = cal.shift(day, -1)
        if prev is None:
            continue
        if any(day not in s or prev not in s for s in (px, d2, dxy, vix)):
            continue  # 缺观测整条剔除，不填补
        row = {
            "d2": d2[day] - d2[prev],
            "dxy": math.log(dxy[day] / dxy[prev]),
            "dvix": vix[day] - vix[prev],
            "r0": math.log(px[day] / px[prev]),
        }
        for name, n in (("r1", 1), ("r5", 5), ("r20", 20), ("r60", LONG_LOOKBACK)):
            fwd = cal.shift(day, n)
            row[name] = math.log(px[fwd] / px[day]) if fwd in px else None
        back = cal.shift(day, -TREND_LOOKBACK)
        row["trend20"] = math.log(px[day] / px[back]) if back in px else None
        back60 = cal.shift(day, -LONG_LOOKBACK)
        row["cum2y"] = d2[day] - d2[back60] if back60 in d2 else None
        feats[day] = row
    return cal, feats


def load_event_days() -> dict:
    days = defaultdict(set)
    with EVENTS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            days[date.fromisoformat(row["event_day"])].add(row["category"])
    return dict(days)


# ---------------------------------------------------------------- 方向规则（第 6 节）


def direction_for(cat: str, f: dict):
    d2, dxy, dvix, trend = f["d2"], f["dxy"], f["dvix"], f["trend20"]
    if cat in US_CATS:
        if d2 > 0 and dxy > 0:
            return "up"
        if d2 < 0 and dxy < 0:
            return "down"
        return None                      # 两个代理符号冲突 -> 无方向
    if cat in CN_CATS:
        if dxy > 0:
            return "up"
        if dxy < 0:
            return "down"
        return None
    if cat == "TRADE":
        if dxy > 0 and dvix > 0:
            return "up"
        if dxy < 0 and dvix < 0:
            return "down"
        return None
    if cat == "FX_POL":
        if trend is None or trend == 0:
            return None
        return "down" if trend > 0 else "up"   # 逆当前 20 日趋势
    return None


def event_direction(cats, f: dict):
    dirs = {d for d in (direction_for(c, f) for c in cats) if d is not None}
    if len(dirs) != 1:
        return None                      # 空集或方向冲突 -> 整日剔除
    return dirs.pop()


def baseline_direction(f: dict, flavor: str):
    if flavor == "B1":
        if f["d2"] > 0 and f["dxy"] > 0:
            return "up"
        if f["d2"] < 0 and f["dxy"] < 0:
            return "down"
        return None
    if f["dxy"] > 0:
        return "up"
    if f["dxy"] < 0:
        return "down"
    return None


def passes_surprise(f: dict, strict: bool = False) -> bool:
    a, b = (STRICT_D2, STRICT_DXY) if strict else (SURPRISE_D2, SURPRISE_DXY)
    return abs(f["d2"]) >= a or abs(f["dxy"]) >= b


# ---------------------------------------------------------------- 评估


def hit(direction: str, ret: float):
    if ret == 0:
        return None
    return (ret > 0) == (direction == "up")


def score(samples) -> dict:
    marks = [h for d, r in samples if (h := hit(d, r)) is not None]
    n, k = len(marks), sum(marks)
    return {
        "n": n,
        "hits": k,
        "hit_rate": round(k / n, 4) if n else None,
        "p_binom": round(binom_p_one_sided(k, n), 4) if n else None,
    }


def in_range(d: date, lo: str, hi: str) -> bool:
    return lo <= d.isoformat() <= hi


def collect(feats, event_days, lo, hi, strict, window="r1"):
    out = []
    for day, cats in sorted(event_days.items()):
        f = feats.get(day)
        if f is None or f[window] is None or not in_range(day, lo, hi):
            continue
        if not passes_surprise(f, strict):
            continue
        d = event_direction(cats, f)
        if d is None:
            continue
        out.append((d, f[window]))
    return out


def collect_baseline(feats, lo, hi, flavor, window="r1"):
    out = []
    for day, f in sorted(feats.items()):
        if f[window] is None or not in_range(day, lo, hi):
            continue
        d = baseline_direction(f, flavor)
        if d is None:
            continue
        out.append((d, f[window]))
    return out


def placebo(samples, real_rate: float) -> dict:
    """在事件日之间随机重排方向标签；保留日历与波动聚集，只打乱标签。"""
    rng = random.Random(SEED)
    dirs = [d for d, _ in samples]
    rets = [r for _, r in samples]
    ge = 0
    rates = []
    for _ in range(N_PLACEBO):
        rng.shuffle(dirs)
        marks = [h for d, r in zip(dirs, rets) if (h := hit(d, r)) is not None]
        rate = sum(marks) / len(marks) if marks else 0.0
        rates.append(rate)
        if rate >= real_rate:
            ge += 1
    rates.sort()
    return {
        "n_runs": N_PLACEBO,
        "share_ge_real": round(ge / N_PLACEBO, 4),
        "placebo_median": round(rates[N_PLACEBO // 2], 4),
        "placebo_p95": round(rates[int(N_PLACEBO * 0.95)], 4),
    }


def volatility_test(feats, event_days, lo, hi, strict=False) -> dict:
    ev, non = [], []
    for day, f in feats.items():
        if f["r1"] is None or not in_range(day, lo, hi):
            continue
        cats = event_days.get(day)
        is_event = bool(cats) and passes_surprise(f, strict) and event_direction(cats, f) is not None
        (ev if is_event else non).append(abs(f["r1"]))
    if len(ev) < 10 or len(non) < 10:
        return {"n_event": len(ev), "n_non_event": len(non), "note": "样本不足"}
    from scipy.stats import mannwhitneyu  # 仅此处需要 scipy

    med_e = sorted(ev)[len(ev) // 2]
    med_n = sorted(non)[len(non) // 2]
    u = mannwhitneyu(ev, non, alternative="greater")
    return {
        "n_event": len(ev),
        "n_non_event": len(non),
        "median_event": round(med_e, 6),
        "median_non_event": round(med_n, 6),
        "ratio": round(med_e / med_n, 4) if med_n else None,
        "mannwhitney_p": round(float(u.pvalue), 4),
    }


def long_horizon(cal, feats, event_days) -> dict:
    """状态变量对 r20/r60 的方向解释力，只用非重叠样本。"""
    evt_dir = {}
    for day, cats in event_days.items():
        f = feats.get(day)
        if f is None or not passes_surprise(f):
            continue
        d = event_direction(cats, f)
        if d:
            evt_dir[day] = 1 if d == "up" else -1

    days = [d for d in cal.dates if d in feats and in_range(d, TRAIN_START, OOS_END)]
    out = {}
    for var, window, step in (("cum2y", "r20", 20), ("cum2y", "r60", 60),
                              ("cum_evt", "r20", 20), ("cum_evt", "r60", 60)):
        samples = []
        for i in range(0, len(days), step):
            day = days[i]
            f = feats[day]
            if f[window] is None:
                continue
            if var == "cum2y":
                v = f["cum2y"]
            else:
                lo_i = max(0, i - LONG_LOOKBACK)
                v = sum(evt_dir.get(x, 0) for x in days[lo_i:i + 1])
            if v is None or v == 0:
                continue
            samples.append(("up" if v > 0 else "down", f[window]))
        out[var + "->" + window] = score(samples)
    return out


def coverage(feats, event_days) -> dict:
    c = Counter()
    for day, cats in event_days.items():
        f = feats.get(day)
        if f is None or not in_range(day, TRAIN_START, OOS_END):
            continue
        c["event_days_with_features"] += 1
        if not passes_surprise(f):
            c["dropped_no_surprise"] += 1
            continue
        d = event_direction(cats, f)
        c["signal_" + (d or "none_conflict_or_empty")] += 1
    return dict(c)


# ---------------------------------------------------------------- 主流程


def run() -> dict:
    cal, feats = build_features()
    event_days = load_event_days()
    report = {
        "frozen_rules": "docs/event-driven-plan.md",
        "seed": SEED,
        "quote_days": len([d for d in feats if in_range(d, TRAIN_START, OOS_END)]),
        "event_days_raw": len(event_days),
    }

    for tier, strict in (("main_3bp_0.2pct", False), ("strict_10bp_0.5pct", True)):
        tr = collect(feats, event_days, TRAIN_START, TRAIN_END, strict)
        oo = collect(feats, event_days, OOS_START, OOS_END, strict)
        block = {"train": score(tr), "oos": score(oo)}
        if oo and block["oos"]["hit_rate"] is not None:
            block["placebo_oos"] = placebo(oo, block["oos"]["hit_rate"])
        for flavor in ("B1", "B2"):
            s_tr = score(collect_baseline(feats, TRAIN_START, TRAIN_END, flavor))
            s_oo = score(collect_baseline(feats, OOS_START, OOS_END, flavor))
            block["baseline_" + flavor] = {"train": s_tr, "oos": s_oo}
            if block["oos"]["n"] and s_oo["n"]:
                block["incremental_" + flavor + "_oos_p"] = round(
                    two_prop_p(block["oos"]["hits"], block["oos"]["n"], s_oo["hits"], s_oo["n"]), 4)
        for w in ("r0", "r5"):
            block["oos_" + w] = score(collect(feats, event_days, OOS_START, OOS_END, strict, window=w))
        report[tier] = block

    report["volatility"] = {
        "train": volatility_test(feats, event_days, TRAIN_START, TRAIN_END),
        "oos": volatility_test(feats, event_days, OOS_START, OOS_END),
    }
    report["long_horizon_descriptive"] = long_horizon(cal, feats, event_days)
    report["direction_coverage"] = coverage(feats, event_days)
    return report


def main() -> None:
    rep = run()
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print("\n[study] -> " + str(REPORT))


if __name__ == "__main__":
    main()
