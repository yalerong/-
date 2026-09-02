"""月均汇率基准。

为什么要有这个：工具原来的绩效口径是"锁汇贡献 vs 到期实际汇率"，
那是**交易员视角**。企业财务按月均汇率记账和考核，司库被问的是
"你的结汇均价比当月平均好多少"——这是**司库视角**。同一笔操作，
两个口径能给出相反的结论。

月均汇率的来源，按可信度排：

1. `state["monthly_average_rates"]`：企业财务给的记账汇率。**这是正解**，
   工具只负责存和用，不去替财务定义什么叫"月均"。
2. 本地行情文件 `data/market/*.csv` 的当月算术平均。只有 USD 有
   （CFETS 中间价 / FRED 日频），且中间价与企业实际成交价有价差，
   所以算出来的数标了 `source`，不冒充官方口径。
3. 都没有 -> 返回 None，页面显示"无月均基准"，不猜。
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKET_DIR = ROOT / "data" / "market"

# 币种 -> (文件名, 口径说明)。只登记确实是「CNY 每 1 外币」的序列。
MARKET_SERIES = {
    "USD": [
        ("CNY_FIXING.csv", "CFETS 人民币兑美元中间价月均"),
        ("DEXCHUS.csv", "FRED USD/CNY 日频月均"),
    ],
}

_CACHE: dict[str, dict[str, float]] = {}


def _monthly_means(filename: str) -> dict[str, float]:
    if filename in _CACHE:
        return _CACHE[filename]
    path = MARKET_DIR / filename
    buckets: dict[str, list[float]] = defaultdict(list)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    date = (row.get("date") or "").strip()
                    raw = (row.get("value") or "").strip()
                    if len(date) < 7 or not raw:
                        continue
                    try:
                        value = float(raw)
                    except ValueError:
                        continue
                    if value <= 0:
                        continue
                    buckets[date[:7]].append(value)
        except OSError:
            buckets = defaultdict(list)
    means = {period: sum(values) / len(values) for period, values in buckets.items() if values}
    _CACHE[filename] = means
    return means


def monthly_average(state: dict, period: str, currency: str) -> tuple[float | None, str | None]:
    """返回 (月均汇率, 来源说明)；取不到就是 (None, None)。"""
    manual = (state.get("monthly_average_rates") or {})
    for key in (f"{period}:{currency}", period):
        value = manual.get(key)
        if isinstance(value, dict):
            value = value.get(currency)
        if value:
            try:
                return float(value), "财务录入的记账月均汇率"
            except (TypeError, ValueError):
                pass

    for filename, label in MARKET_SERIES.get(currency, []):
        value = _monthly_means(filename).get(period)
        if value:
            return value, label

    return None, None


def benchmark_row(
    period: str,
    currency: str,
    gross_signed: float,
    hedges: list[dict],
    actual_rate: float,
    average_rate: float | None,
    average_source: str | None,
) -> dict | None:
    """把一个（期间 × 币种）的实际结果折算成结汇均价，再和月均比。

    实际结果 = 已锁部分按锁定价 + 未锁部分按到期实际价。
    基准 = 全部按月均汇率。
    差额对收汇方是"多收的人民币"，对付汇方是"少付的人民币"，
    所以按敞口方向定符号，正数一律表示比基准好。
    """
    notional = abs(gross_signed)
    if notional <= 0 or not average_rate:
        return None

    hedged_notional = 0.0
    hedged_value = 0.0
    for hedge in hedges:
        amount = abs(float(hedge.get("amount", 0) or 0))
        rate = float(hedge.get("locked_rate", 0) or 0)
        if amount <= 0 or rate <= 0:
            continue
        # 锁得比敞口多的部分不算进结汇均价——那是超额套保，不是这笔业务的成本
        usable = min(amount, notional - hedged_notional)
        if usable <= 0:
            break
        hedged_notional += usable
        hedged_value += usable * rate

    unhedged = max(0.0, notional - hedged_notional)
    realized_avg = (hedged_value + unhedged * actual_rate) / notional
    sign = 1.0 if gross_signed > 0 else -1.0

    # 两因子归因：套保把价格从"到期即期"挪到了"结汇均价"；
    # 市场本身把价格从"月均"挪到了"到期即期"。两段加起来就是总差异。
    hedge_effect = sign * notional * (realized_avg - actual_rate)
    timing_effect = sign * notional * (actual_rate - average_rate)

    return {
        "period": period,
        "currency": currency,
        "notional": notional,
        "hedged_notional": hedged_notional,
        "hedge_coverage": hedged_notional / notional if notional else 0.0,
        "realized_avg_rate": realized_avg,
        "actual_rate": actual_rate,
        "average_rate": average_rate,
        "average_source": average_source,
        "hedge_effect_cny": hedge_effect,
        "timing_effect_cny": timing_effect,
        "vs_benchmark_cny": hedge_effect + timing_effect,
        "beats_benchmark": (hedge_effect + timing_effect) > 0,
    }
