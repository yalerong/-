"""拉取 FRED / 美联储 H.10 日频序列，落地为本地 CSV。

单一权威源，口径见 docs/event-driven-plan.md 第 2 节。
本模块只负责取数和数据质量报告，不做任何收益计算。

    python -m events.market_fetch
    python -m events.market_fetch --series DEXCHUS --series DGS2
    python -m events.market_fetch --report-only
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
MARKET_DIR = ROOT / "data" / "market"
USER_AGENT = "FX-Hedge-Lab/events"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# 序列清单与方向约定必须与 docs/event-driven-plan.md 第 2 节一致
SERIES: dict[str, str] = {
    "DEXCHUS": "人民币兑1美元（在岸，纽约中午买入价）；上升=人民币走弱",
    "DEXJPUS": "日元兑1美元；上升=日元走弱",
    "DTWEXBGS": "美联储广义美元指数；上升=美元走强",
    "DGS2": "2年期美债收益率(%)；上升=美国利率预期上行",
    "DGS10": "10年期美债收益率(%)",
    "VIXCLS": "VIX；上升=避险",
}

# FRED 用 "." 表示该交易日无观测（节假日或缺失）
MISSING = "."


def _download(series_id: str) -> str:
    url = FRED_CSV.format(sid=series_id)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def parse_fred_csv(text: str) -> list[tuple[str, float]]:
    """解析 fredgraph.csv，丢弃 '.' 缺失行。返回按日期升序的 (date, value)。"""
    rows: list[tuple[str, float]] = []
    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if not header or len(header) < 2:
        raise RuntimeError("unexpected FRED csv header: %r" % (header,))
    for row in reader:
        if len(row) < 2:
            continue
        ds, raw = row[0].strip(), row[1].strip()
        if not ds or raw == MISSING or raw == "":
            continue
        try:
            rows.append((ds, float(raw)))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def write_series(series_id: str, rows: list[tuple[str, float]]) -> Path:
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    path = MARKET_DIR / f"{series_id}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "value"])
        for ds, v in rows:
            w.writerow([ds, f"{v:.6f}"])
    return path


def load_series(series_id: str) -> list[tuple[str, float]]:
    path = MARKET_DIR / f"{series_id}.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在，先跑 python -m events.market_fetch")
    out: list[tuple[str, float]] = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append((row["date"], float(row["value"])))
    out.sort(key=lambda r: r[0])
    return out


def fetch_series(series_id: str) -> Path:
    rows = parse_fred_csv(_download(series_id))
    if not rows:
        raise RuntimeError(f"{series_id}: FRED 未返回任何观测")
    path = write_series(series_id, rows)
    print(f"[fetch] {series_id}: {len(rows)} obs {rows[0][0]}..{rows[-1][0]} -> {path}")
    return path


def _weekday_gaps(dates: list[str], min_days: int = 5) -> list[tuple[str, str, int]]:
    """找出连续观测之间跨过 >= min_days 个工作日的空洞。"""
    gaps: list[tuple[str, str, int]] = []
    for prev, cur in zip(dates, dates[1:]):
        d0 = date.fromisoformat(prev)
        d1 = date.fromisoformat(cur)
        n = 0
        cursor = d0 + timedelta(days=1)
        while cursor < d1:
            if cursor.weekday() < 5:
                n += 1
            cursor += timedelta(days=1)
        if n >= min_days:
            gaps.append((prev, cur, n))
    return gaps


def report(series_ids: list[str], since: str = "2019-06-27") -> dict:
    """数据质量报告：覆盖区间、研究窗口内观测数、工作日空洞。"""
    out: dict[str, dict] = {}
    for sid in series_ids:
        try:
            rows = load_series(sid)
        except FileNotFoundError as exc:
            print(f"[report] {sid}: {exc}")
            continue
        dates = [d for d, _ in rows]
        in_window = [d for d in dates if d >= since]
        gaps = _weekday_gaps(in_window)
        out[sid] = {
            "n_total": len(dates),
            "first": dates[0],
            "last": dates[-1],
            "n_in_window": len(in_window),
            "gaps": gaps,
        }
        print(
            f"[report] {sid:9s} {len(dates):6d} obs  {dates[0]}..{dates[-1]}  "
            f"研究窗口内 {len(in_window)} 条  工作日空洞 {len(gaps)} 处"
        )
        for a, b, n in gaps[:5]:
            print(f"           gap {a} -> {b} ({n} 个工作日无观测)")
        if len(gaps) > 5:
            print(f"           ... 另有 {len(gaps) - 5} 处")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", action="append", choices=sorted(SERIES), help="可重复，默认全部")
    parser.add_argument("--since", default="2019-06-27", help="数据质量报告的研究窗口起点")
    parser.add_argument("--report-only", action="store_true", help="不下载，只对本地 CSV 出报告")
    args = parser.parse_args()

    ids = args.series or sorted(SERIES)
    if not args.report_only:
        for sid in ids:
            fetch_series(sid)
    report(ids, since=args.since)


if __name__ == "__main__":
    main()
