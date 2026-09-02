"""远期汇率。

以前的问题：锁汇建议里的"交易汇率"直接用的即期价，等于假设远期点 = 0。
中美利差 2~3%、半年期限，这一项就有 1% 以上的系统性偏差——**比预测折扣
闸门最多 0.5× 的影响还大**。远期结汇的价格从来不是即期。

用抛补利率平价（CIP）从利差推：

    F = S × (1 + i_基础货币 × t) / (1 + i_外币 × t)

其中 S 是「1 外币 = 多少 CNY」，i 是年化单利，t 是到期年限。用单利
是货币市场惯例（一年以内的掉期都这么报）。CIP 在主流货币对上是套利
约束，偏离只有几个 bp；人民币有资本管制，境内外会有基差，所以：

**只要企业能从银行拿到真实报价，就应该用报价覆盖掉这里算出来的数。**
`forward_overrides` 就是干这个的，覆盖后 basis 标成 `quote`。
"""
from __future__ import annotations

from datetime import date

DAYS_PER_YEAR = 365.0


def _period_end(period: str) -> date:
    year, month = int(period[:4]), int(period[5:7])
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1).fromordinal(date(year, month + 1, 1).toordinal() - 1)


def period_end_iso(period: str) -> str:
    """该期间月末，ISO 日期。远期定价、会计科目、生成的锁汇单共用这一个日期。"""
    try:
        return _period_end(period).isoformat()
    except (ValueError, IndexError):
        return f"{period}-28"


def tenor_years(period: str, today: date | None = None) -> float:
    """从今天到该期间月末的年限，最少给一天，不给负数。"""
    today = today or date.today()
    try:
        end = _period_end(period)
    except (ValueError, IndexError):
        return 0.0
    days = (end - today).days
    return max(days, 0) / DAYS_PER_YEAR


def forward_rate(
    spot: float,
    currency: str,
    period: str,
    config: dict,
    today: date | None = None,
) -> dict:
    """返回 {rate, points, basis, tenor_years, note}。

    basis 三种：
      quote  —— 用了 forward_overrides 里的银行报价
      cip    —— 用利差推的
      spot   —— 缺利率，退回即期，并在 note 里说明
    """
    t = tenor_years(period, today)
    if t <= 0:
        # 到期日已过就没有远期可言。这一步必须在读报价之前——
        # 配置里留着的旧报价会把已经过期的期间标成「银行报价」，
        # 拿一个作废的价格去算中性情景损益。
        return {
            "rate": spot,
            "points": 0.0,
            "basis": "spot",
            "tenor_years": 0.0,
            "note": "到期日已过或就在今天，远期点为 0",
        }

    overrides = config.get("forward_overrides") or {}
    for key, needs_dict in ((f"{period}:{currency}", False), (period, True)):
        value = overrides.get(key)
        # 只写月份不写币种的 key，值必须是 {币种: 报价}。
        # {"2027-06": 7.05} 是个美元报价，套到欧元上会差 10% 还标着「银行报价」。
        if isinstance(value, dict):
            value = value.get(currency)
        elif needs_dict:
            continue
        if value:
            try:
                rate = float(value)
            except (TypeError, ValueError):
                continue
            if rate > 0:
                return {
                    "rate": rate,
                    "points": rate - spot,
                    "basis": "quote",
                    "tenor_years": t,
                    "note": "银行远期报价（配置里录入）",
                }

    rates = config.get("interest_rates") or {}
    base_currency = config.get("base_currency", "CNY")
    i_base = rates.get(base_currency)
    i_foreign = rates.get(currency)

    if i_base is None or i_foreign is None:
        missing = [name for name, value in ((base_currency, i_base), (currency, i_foreign)) if value is None]
        return {
            "rate": spot,
            "points": 0.0,
            "basis": "spot",
            "tenor_years": t,
            "note": f"缺 {'/'.join(missing)} 的利率，暂用即期价",
        }

    rate = spot * (1 + float(i_base) * t) / (1 + float(i_foreign) * t)
    return {
        "rate": rate,
        "points": rate - spot,
        "basis": "cip",
        "tenor_years": t,
        "note": f"按利差推算：{base_currency} {float(i_base):.2%} / {currency} {float(i_foreign):.2%}，期限 {t:.2f} 年",
    }
