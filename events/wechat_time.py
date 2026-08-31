"""微信导出时间 -> 北京时间。口径与验证见 docs/event-driven-plan.md 第 3 节修订一。

导出脚本用 datetime.fromtimestamp() 取的是**导出机器的本地时间**，
该机器时区为 Eastern Standard Time（固定 UTC-5，已关闭夏令时），
因此 CSV 的 time 列是 UTC-5，北京时间 = 该值 + 13 小时（固定偏移，无 DST 分支）。

禁止在别处直接使用 CSV 原始时间值。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .calendar import BEIJING

# 导出机器时区固定 UTC-5 且不随夏令时切换；北京 UTC+8
EXPORT_TZ = timezone(timedelta(hours=-5))
OFFSET_HOURS = 13

# 早报标题里的日期，用于反向验证偏移量是否正确
ZAOBAO_DATE = re.compile(r"(?:(\d{2,4})\s*[年.\-/])?\s*(\d{1,2})\s*[月.\-/]\s*(\d{1,2})\s*日?\s*早报")
MIN_TITLE_MATCH_RATE = 0.95


def to_beijing(value: str | datetime) -> datetime:
    """CSV 的 time 字段 -> 带时区的北京时间。"""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip())
    if value.tzinfo is None:
        value = value.replace(tzinfo=EXPORT_TZ)
    return value.astimezone(BEIJING)


def title_date(content: str) -> tuple[int, int] | None:
    """从早报标题里取 (月, 日)；取不到返回 None。"""
    m = ZAOBAO_DATE.search(content[:40])
    if not m:
        return None
    return int(m.group(2)), int(m.group(3))


def verify_offset(rows: list[dict], strict: bool = True) -> dict:
    """用早报标题自带的日期反向验证时区偏移。

    早报标题写的是当天北京日期。若换算正确，两者应高度一致。
    这是防止"整批事件被系统性错置 13 小时"的自检，任何抽取流程开跑前必须通过。
    """
    checked = matched = 0
    mismatches: list[tuple[str, str]] = []
    for row in rows:
        content = row.get("content") or ""
        md = title_date(content)
        if md is None:
            continue
        checked += 1
        bj = to_beijing(row["time"])
        if (bj.month, bj.day) == md:
            matched += 1
        elif len(mismatches) < 10:
            mismatches.append((row["time"], content[:30]))
    rate = matched / checked if checked else 0.0
    result = {"checked": checked, "matched": matched, "rate": rate, "mismatches": mismatches}
    if strict and (checked < 100 or rate < MIN_TITLE_MATCH_RATE):
        raise RuntimeError(
            f"时区自检未通过：早报标题日期匹配率 {rate:.1%}（{matched}/{checked}），"
            f"低于 {MIN_TITLE_MATCH_RATE:.0%}。偏移量或导出机器时区可能已变，先查清再跑。"
        )
    return result
