"""补齐两个境内定价变量：人民币中间价（CFETS）与离岸人民币 CNH（东财）。

这两条是 docs/event-driven-results.md 第五节列出的头两个缺口。
本模块只取数，不参与任何已完成的研究——PR#4 的结论不因新增数据而改写。

口径说明（重要，写进 docs/event-driven-plan-v2.md）：

- **中间价**来自中国外汇交易中心（CFETS），是人民币中间价的官方发布方，属权威源。
  每交易日**北京时间 09:15 公布，早于境内即期市场 09:30 开盘**，因此它是当日
  可用的前置信息，不是事后数据——这一点决定了它可以进当日决策。
- **CNH**来自东方财富转手数据，**不是权威源**，且其收盘时点与 FRED 的纽约中午
  买入价不同。任何把 CNH 与 DEXCHUS 直接相减得到的"离岸-在岸价差"都带有时点
  错配，必须在结论里标注。

本机若开着代理，会打断到境内站点的连接，因此这里显式绕过代理。

    python -m events.cn_fetch
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
MARKET_DIR = ROOT / "data" / "market"
UA = "Mozilla/5.0 (compatible; FX-Hedge-Lab/events)"

CCPR_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EM_CNH_SECID = "133.USDCNH"

# 绕开本机代理；境内站点走代理会被打断
_opener = build_opener(ProxyHandler({}))


def _post(url: str, form: dict) -> dict:
    req = Request(url, data=urlencode(form).encode(), headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    with _opener.open(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(url: str, params: dict) -> dict:
    req = Request(f"{url}?{urlencode(params)}", headers={"User-Agent": UA})
    with _opener.open(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def write_series(series_id: str, rows: list[tuple[str, float]]) -> Path:
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    path = MARKET_DIR / f"{series_id}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for ds, v in sorted(set(rows)):
            w.writerow([ds, f"{v:.6f}"])
    return path


def fetch_fixing(start: str = "2019-01-01", end: str | None = None,
                 pair: str = "USD/CNY") -> Path:
    """CFETS 人民币汇率中间价历史，按年分块拉取。"""
    end = end or date.today().isoformat()
    rows: list[tuple[str, float]] = []
    for year in range(int(start[:4]), int(end[:4]) + 1):
        lo = max(start, f"{year}-01-01")
        hi = min(end, f"{year}-12-31")
        page = 1
        while True:
            payload = _post(CCPR_URL, {
                "startDate": lo, "endDate": hi, "currency": pair,
                "pageNum": page, "pageSize": 500})
            recs = payload.get("records") or []
            for r in recs:
                try:
                    rows.append((r["date"], float(r["values"][0])))
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
            head = payload.get("data") or {}
            if page >= int(head.get("pageTotal") or 1) or not recs:
                break
            page += 1
    if not rows:
        raise RuntimeError("CFETS 未返回中间价数据")
    path = write_series("CNY_FIXING", rows)
    uniq = sorted(set(rows))
    print(f"[fetch] CNY_FIXING: {len(uniq)} obs {uniq[0][0]}..{uniq[-1][0]} -> {path}")
    return path


def fetch_cnh(start: str = "20190101") -> Path:
    """东财美元兑离岸人民币日收盘。非权威源，口径见模块文档。"""
    payload = _get(EM_KLINE, {
        "secid": EM_CNH_SECID, "klt": 101, "fqt": 0,
        "beg": start, "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5", "fields2": "f51,f53"})
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows: list[tuple[str, float]] = []
    for line in klines:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            rows.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    if not rows:
        raise RuntimeError("东财未返回 USDCNH 数据")
    path = write_series("USDCNH", rows)
    print(f"[fetch] USDCNH ({data.get('name')}): {len(rows)} obs "
          f"{rows[0][0]}..{rows[-1][0]} -> {path}")
    return path


def sanity_check() -> dict:
    """与 DEXCHUS 对齐后的量级自检，防止拿错序列或单位。"""
    from . import market_fetch

    onshore = dict(market_fetch.load_series("DEXCHUS"))
    fixing = dict(market_fetch.load_series("CNY_FIXING"))
    cnh = dict(market_fetch.load_series("USDCNH"))
    common = sorted(set(onshore) & set(fixing) & set(cnh))
    if not common:
        raise RuntimeError("三条序列没有共同交易日，检查日期格式")
    dev_fix = [onshore[d] / fixing[d] - 1 for d in common]
    dev_cnh = [cnh[d] / onshore[d] - 1 for d in common]
    out = {
        "common_days": len(common),
        "span": [common[0], common[-1]],
        "spot_vs_fixing_mean_pct": round(100 * sum(dev_fix) / len(dev_fix), 4),
        "spot_vs_fixing_abs_max_pct": round(100 * max(abs(x) for x in dev_fix), 4),
        "cnh_vs_onshore_mean_pct": round(100 * sum(dev_cnh) / len(dev_cnh), 4),
        "cnh_vs_onshore_abs_max_pct": round(100 * max(abs(x) for x in dev_cnh), 4),
    }
    # 中间价日浮动区间是 ±2%，但 DEXCHUS 是纽约中午价、与 09:15 的中间价差半天，
    # 因此这里只做量级检查，不当作违反区间的证据。
    if out["spot_vs_fixing_abs_max_pct"] > 5:
        raise RuntimeError(f"即期对中间价偏离超过 5%，疑似拿错序列：{out}")
    if out["cnh_vs_onshore_abs_max_pct"] > 5:
        raise RuntimeError(f"CNH 对在岸偏离超过 5%，疑似拿错序列：{out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    if not args.skip_fetch:
        fetch_fixing()
        fetch_cnh()
    print(json.dumps(sanity_check(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
