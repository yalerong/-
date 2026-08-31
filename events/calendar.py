"""事件日归属与窗口日期。规则冻结于 docs/event-driven-plan.md 第 3、7 节。

本模块只做日期归属，不计算任何收益。

归属规则（不允许在看到结果之后调整）：
    把新闻原始发布时间转换为 America/New_York 本地时间
    该时刻 <= 当日 12:00  -> 事件日 D = 该纽约日期
    该时刻 >  当日 12:00  -> 事件日 D = 下一个纽约日期
    再把 D 前滚到报价序列中实际有观测的最近一天

依据：DEXCHUS 是纽约中午买入价，北京时间约次日 00:00。北京 D 日 20:30
发布的美国数据对应纽约 D 日 08:30，早于当日中午，落在 DEXCHUS[D]。
"""
from __future__ import annotations

from datetime import date, datetime, time

try:
    from zoneinfo import ZoneInfo
except ImportError as exc:  # pragma: no cover - 3.9+ 标准库
    raise RuntimeError("需要 Python 3.9+ 的 zoneinfo；Windows 上还需 pip install tzdata") from exc

BEIJING = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
NY_CUTOFF = time(12, 0)


def beijing(value: str | datetime) -> datetime:
    """把北京时间（'YYYY-MM-DD HH:MM:SS' 或 naive datetime）标成带时区的 datetime。

    微信导出的 time 字段是本机北京时间，用这个函数显式打时区标签，
    不允许把裸 naive datetime 直接送进 to_event_day。
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip())
    if value.tzinfo is not None:
        raise ValueError("beijing() 只接受 naive 时间；已带时区的直接传给 to_event_day")
    return value.replace(tzinfo=BEIJING)


def to_event_day(news_dt: datetime) -> date:
    """新闻原始发布时间 -> 事件日（纽约日历日，未做节假日前滚）。"""
    if news_dt.tzinfo is None:
        raise ValueError("news_dt 必须带时区；北京时间请先经 beijing() 包装")
    ny = news_dt.astimezone(NEW_YORK)
    if ny.timetz().replace(tzinfo=None) <= NY_CUTOFF:
        return ny.date()
    return date.fromordinal(ny.date().toordinal() + 1)


class QuoteCalendar:
    """报价日历：由主标的（DEXCHUS）实际有观测的日期构成。"""

    def __init__(self, dates: list[str] | list[date]) -> None:
        norm = [d if isinstance(d, date) else date.fromisoformat(d) for d in dates]
        self._dates = sorted(set(norm))
        self._pos = {d: i for i, d in enumerate(self._dates)}
        if not self._dates:
            raise ValueError("报价日历为空")

    @classmethod
    def from_series(cls, series_id: str = "DEXCHUS") -> "QuoteCalendar":
        from . import market_fetch

        return cls([d for d, _ in market_fetch.load_series(series_id)])

    @property
    def dates(self) -> list[date]:
        return list(self._dates)

    def roll_forward(self, d: date) -> date | None:
        """前滚到最近一个有报价的交易日；超出序列末尾返回 None。"""
        if d in self._pos:
            return d
        for cur in self._dates:
            if cur > d:
                return cur
        return None

    def shift(self, d: date, n: int) -> date | None:
        """在报价日历上相对 d 平移 n 个交易日；越界返回 None。"""
        i = self._pos.get(d)
        if i is None:
            return None
        j = i + n
        if j < 0 or j >= len(self._dates):
            return None
        return self._dates[j]

    def window_dates(self, event_day: date) -> dict[str, date | None] | None:
        """给出 r0/r1/r5 三个窗口所需的全部日期端点。

        r0 = D-1 -> D（反应确认，不进主判据）
        r1 = D   -> D+1（主判据）
        r5 = D   -> D+5（持续性）
        任一端点缺失时对应窗口为 None，由上层决定是否剔除该事件。
        """
        d = self.roll_forward(event_day)
        if d is None:
            return None
        return {
            "prev": self.shift(d, -1),
            "d": d,
            "d1": self.shift(d, 1),
            "d5": self.shift(d, 5),
        }
