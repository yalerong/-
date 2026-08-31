"""v2 事件研究：境内定价变量（中间价 + CNH）。

规则冻结于 docs/event-driven-plan-v2.md，本模块不做任何参数搜索。
样本外只跑一次，且这是该窗口上的第二次也是最后一次检验。

    python -m events.event_study_v2
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from . import market_fetch
from .calendar import QuoteCalendar
from .event_study import (
    OOS_END,
    OOS_START,
    TRAIN_END,
    TRAIN_START,
    in_range,
    load_event_days,
    placebo,
    score,
    two_prop_p,
)

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "events" / "study_report_v2.json"

# ---- 冻结阈值（docs/event-driven-plan-v2.md 第 3 节，取自训练期解释变量分位数） ----
MAIN_FIX, MAIN_CNH = 0.0010, 0.0017        # 主档：0.10% / 0.17%，约上 50%
STRICT_FIX, STRICT_CNH = 0.0030, 0.0042    # 严档：0.30% / 0.42%，约上 15%


def build_features() -> tuple[QuoteCalendar, dict]:
    cal = QuoteCalendar.from_series("DEXCHUS")
    spot = {date.fromisoformat(d): v for d, v in market_fetch.load_series("DEXCHUS")}
    fix = {date.fromisoformat(d): v for d, v in market_fetch.load_series("CNY_FIXING")}
    cnh = {date.fromisoformat(d): v for d, v in market_fetch.load_series("USDCNH")}

    feats: dict = {}
    for day in cal.dates:
        p1, p2 = cal.shift(day, -1), cal.shift(day, -2)
        if p1 is None or p2 is None:
            continue
        if day not in spot or p1 not in spot or p2 not in spot:
            continue
        row: dict = {}
        # 中间价意外：中间价自身变动 减去 前一日即期变动（差分掉持续水平偏离）
        if day in fix and p1 in fix:
            row["fix_surp"] = math.log(fix[day] / fix[p1]) - math.log(spot[p1] / spot[p2])
        else:
            row["fix_surp"] = None
        row["dcnh"] = math.log(cnh[day] / cnh[p1]) if day in cnh and p1 in cnh else None
        # rF 规则只能用 D-1 收盘的离岸变动（早于 D 日 09:15 的中间价）
        row["dcnh_prev"] = math.log(cnh[p1] / cnh[p2]) if p1 in cnh and p2 in cnh else None
        row["cnh_prem"] = math.log(cnh[day] / spot[day]) if day in cnh else None

        nxt, d5 = cal.shift(day, 1), cal.shift(day, 5)
        row["r1"] = math.log(spot[nxt] / spot[day]) if nxt in spot else None
        row["r5"] = math.log(spot[d5] / spot[day]) if d5 in spot else None
        # rF：中间价公布(09:15) -> 当日纽约中午
        row["rF"] = math.log(spot[day] / fix[day]) if day in fix else None
        feats[day] = row
    return cal, feats


# ---------------------------------------------------------------- 方向规则（第 4 节）


def direction_main(f: dict):
    """方向完全由境内变量给出；两者符号冲突则无方向。"""
    a, b = f["fix_surp"], f["dcnh"]
    if a is None or b is None:
        return None
    if a > 0 and b > 0:
        return "up"
    if a < 0 and b < 0:
        return "down"
    return None


def direction_rf(f: dict):
    """rF 窗口专用：只用 D-1 的离岸变动，排除 FIX[D] 以避免机械负相关。"""
    v = f["dcnh_prev"]
    if v is None or v == 0:
        return None
    return "up" if v > 0 else "down"


def passes_surprise(f: dict, strict: bool = False) -> bool:
    a, b = (STRICT_FIX, STRICT_CNH) if strict else (MAIN_FIX, MAIN_CNH)
    fs, dc = f["fix_surp"], f["dcnh"]
    return (fs is not None and abs(fs) >= a) or (dc is not None and abs(dc) >= b)


# ---------------------------------------------------------------- 采样


def collect(feats, event_days, lo, hi, strict, window="r1", news=True):
    out = []
    for day, f in sorted(feats.items()):
        if f[window] is None or not in_range(day, lo, hi):
            continue
        if news and day not in event_days:
            continue
        if not passes_surprise(f, strict):
            continue
        d = direction_main(f)
        if d is None:
            continue
        out.append((d, f[window]))
    return out


def collect_rf(feats, lo, hi):
    out = []
    for day, f in sorted(feats.items()):
        if f["rF"] is None or not in_range(day, lo, hi):
            continue
        d = direction_rf(f)
        if d is None:
            continue
        out.append((d, f["rF"]))
    return out


# ---------------------------------------------------------------- 主流程


def run() -> dict:
    cal, feats = build_features()
    event_days = load_event_days()
    report: dict = {
        "frozen_rules": "docs/event-driven-plan-v2.md",
        "note": "该样本外窗口上的第二次也是最后一次检验；主判据门槛 p<=0.025",
        "quote_days": len([d for d in feats if in_range(d, TRAIN_START, OOS_END)]),
    }

    for tier, strict, tag in (("main", False, "B3"), ("strict", True, "B4")):
        tr = collect(feats, event_days, TRAIN_START, TRAIN_END, strict)
        oo = collect(feats, event_days, OOS_START, OOS_END, strict)
        block = {"train": score(tr), "oos": score(oo)}
        if oo and block["oos"]["hit_rate"] is not None:
            block["placebo_oos"] = placebo(oo, block["oos"]["hit_rate"])
        # 基线：同一套方向规则，去掉新闻条件
        b_tr = score(collect(feats, event_days, TRAIN_START, TRAIN_END, strict, news=False))
        b_oo = score(collect(feats, event_days, OOS_START, OOS_END, strict, news=False))
        block["baseline_" + tag] = {"train": b_tr, "oos": b_oo}
        if block["oos"]["n"] and b_oo["n"]:
            block["incremental_oos_p"] = round(
                two_prop_p(block["oos"]["hits"], block["oos"]["n"], b_oo["hits"], b_oo["n"]), 4)
        block["oos_r5"] = score(collect(feats, event_days, OOS_START, OOS_END, strict, window="r5"))
        report[tier] = block

    report["rF"] = {
        "train": score(collect_rf(feats, TRAIN_START, TRAIN_END)),
        "oos": score(collect_rf(feats, OOS_START, OOS_END)),
    }
    return report


def main() -> None:
    rep = run()
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print("\n[study-v2] -> " + str(REPORT))


if __name__ == "__main__":
    main()
