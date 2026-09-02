"""价差与量差。

司库口径原来只答"结汇均价比月均好多少"，那是**纯价差**。但敞口本身
也会不准——订单黄了一半、客户提前付了、金额改了。这部分差异跟汇率
一点关系没有，混在一起看就会把"订单缩水"记到套保决策头上。

拆法和管报里的价量分析同构：

    计划结果 = 计划敞口 × 基准汇率(当月月均)
    实际结果 = 实际敞口 × 结汇均价

    量差 = (实际敞口 − 计划敞口) × 基准汇率
    价差 = 实际敞口 × (结汇均价 − 基准汇率)
    量差 + 价差 = 实际结果 − 计划结果

价差再往下就是已有的**套保效应 + 择时效应**两块。

还有一个必须点名的后果：敞口缩水但远期已经按计划金额锁了，那就是
**超额套保**——敞口没发生，远期照样得交割，这是企业套保里最常见的
真实损失来源之一。
"""
from __future__ import annotations


def decompose(
    planned_notional: float,
    actual_notional: float | None,
    realized_avg_rate: float,
    benchmark_rate: float,
    hedged_notional: float,
    gross_signed: float,
) -> dict | None:
    """价量分解。actual_notional 为 None 表示还没录实际发生额。"""
    if actual_notional is None or planned_notional is None:
        return None
    if benchmark_rate is None or realized_avg_rate is None:
        return None

    sign = 1.0 if gross_signed > 0 else -1.0
    volume_gap = actual_notional - planned_notional

    volume_variance = sign * volume_gap * benchmark_rate
    price_variance = sign * actual_notional * (realized_avg_rate - benchmark_rate)

    over_hedged = max(0.0, hedged_notional - actual_notional)
    return {
        "planned_notional": planned_notional,
        "actual_notional": actual_notional,
        "volume_gap": volume_gap,
        "volume_gap_pct": (volume_gap / planned_notional) if planned_notional else 0.0,
        "volume_variance_cny": volume_variance,
        "price_variance_cny": price_variance,
        "total_variance_cny": volume_variance + price_variance,
        # 敞口没发生但远期已经锁了——远期照样得交割，这是真实损失
        "over_hedged_notional": over_hedged,
        "over_hedged": over_hedged > 0,
    }
