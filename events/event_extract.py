"""微信群早报 -> 事件表。类目规则冻结于 docs/event-driven-plan.md 第 4 节。

抽取流程只做三件事：换算时间、切分条目、按封闭类目机械分类。
不计算任何收益，不做人工裁量。

    python -m events.event_extract --csv "<导出的聊天消息.csv>"
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .calendar import QuoteCalendar, to_event_day
from .wechat_time import to_beijing, verify_offset

ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = ROOT / "data" / "events"

# ---------------------------------------------------------------- 类目规则（冻结）

US_MARK = re.compile(r"美联储|联储|美国|美元|鲍威尔|FOMC|白宫|华府|美财长|贝森特|特朗普|拜登")
CN_MARK = re.compile(r"中国|央行|人民银行|国务院|发改委|人民币|统计局|商务部|财政部")

MON = r"加息|降息|议息|FOMC|点阵图|缩表|扩表|利率决议|货币政策|会议纪要|褐皮书|联邦基金|宽松|紧缩"
DATA = r"CPI|PPI|PCE|非农|就业|失业|零售|PMI|GDP|通胀|物价|社融|信贷|进出口|工业增加值|消费者信心|贸易帐|耐用品|订单|房价"
CN_POLICY = r"降准|LPR|MLF|逆回购|政治局|国常会|国务院常务|专项债|财政刺激|房地产|地产|楼市|稳增长|减税"
TRADE = r"关税|贸易战|加征|反制|出口管制|实体清单|301调查|制裁|贸易谈判|经贸磋商|地缘|冲突|战争|军事"
FX_POL = r"逆周期因子|外汇存款准备金|离岸央票|中间价|外汇局|汇率维稳|干预汇率|口头干预|外汇准备金"

# (类目, 关键词, 需要的国别标记)；顺序即同分时的冻结优先级
RULES: list[tuple[str, re.Pattern, re.Pattern | None]] = [
    ("FX_POL", re.compile(FX_POL), None),
    ("TRADE", re.compile(TRADE), None),
    ("US_MON", re.compile(MON), US_MARK),
    ("US_DATA", re.compile(DATA), US_MARK),
    ("CN_POL", re.compile(CN_POLICY + "|" + MON), CN_MARK),
    ("CN_DATA", re.compile(DATA), CN_MARK),
]

# ---------------------------------------------------------------- 条目切分

SECTION_PAT = re.compile(r"【?\s*(国际新闻|国际要闻|国内新闻|国内要闻|今日重点关注|重点关注|市场要闻)\s*】?[:：]?")
BULLET_PAT = re.compile(r"^\s*(?:[★*※•·▲◆]+|（\s*\d+\s*）|\(\s*\d+\s*\)|\d+[、.])\s*")
MIN_ITEM_CHARS = 15


def classify(text: str) -> str:
    """按冻结规则表给单条目定类目；同分取 RULES 中靠前者。不匹配返回 other。"""
    best, best_score = "other", 0
    for name, kw, mark in RULES:
        if mark is not None and not mark.search(text):
            continue
        score = len(set(kw.findall(text)))
        if score > best_score:
            best, best_score = name, score
    return best


def split_items(content: str) -> list[tuple[str, str]]:
    """把一条微信消息切成 (小节, 条目正文)。非早报格式的整条作为一个条目。"""
    items: list[tuple[str, str]] = []
    section = "single"
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        text = " ".join(x.strip() for x in buf if x.strip())
        if len(text) >= MIN_ITEM_CHARS:
            items.append((section, text))
        buf.clear()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        sec = SECTION_PAT.match(line)
        if sec:
            flush()
            section = sec.group(1)
            rest = line[sec.end():].strip()
            if rest:
                buf.append(BULLET_PAT.sub("", rest))
            continue
        if BULLET_PAT.match(line):
            flush()
            buf.append(BULLET_PAT.sub("", line))
        else:
            buf.append(line)
    flush()
    return items


# ---------------------------------------------------------------- 主流程


def extract(csv_path: Path, calendar: QuoteCalendar | None = None) -> tuple[list[dict], dict]:
    rows = [r for r in csv.DictReader(csv_path.open(encoding="utf-8-sig")) if r.get("msg_type") == "1"]
    tz_check = verify_offset(rows)  # 不通过直接抛，防止整批事件错置 13 小时
    print(f"[extract] 时区自检通过：早报标题日期匹配 {tz_check['matched']}/{tz_check['checked']} "
          f"({tz_check['rate']:.1%})")

    if calendar is None:
        calendar = QuoteCalendar.from_series("DEXCHUS")

    seen: set[str] = set()
    out: list[dict] = []
    dropped_dup = dropped_other = dropped_nocal = 0

    for row in rows:
        bj = to_beijing(row["time"])
        raw_day = to_event_day(bj)
        event_day = calendar.roll_forward(raw_day)
        for section, text in split_items(row.get("content") or ""):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
            if digest in seen:
                dropped_dup += 1
                continue
            seen.add(digest)
            category = classify(text)
            if category == "other":
                dropped_other += 1
                continue
            if event_day is None:
                dropped_nocal += 1
                continue
            out.append({
                "event_id": digest,
                "msg_time_export": row["time"],
                "msg_time_beijing": bj.strftime("%Y-%m-%d %H:%M:%S"),
                "beijing_date": bj.date().isoformat(),
                "event_day": event_day.isoformat(),
                "section": section,
                "category": category,
                "n_chars": len(text),
                "text": text,
            })

    out.sort(key=lambda r: (r["event_day"], r["category"], r["event_id"]))
    summary = {
        "source_rows": len(rows),
        "tz_check": {k: tz_check[k] for k in ("checked", "matched", "rate")},
        "items_kept": len(out),
        "dropped_duplicate": dropped_dup,
        "dropped_other_category": dropped_other,
        "dropped_no_quote_day": dropped_nocal,
        "by_category": dict(Counter(r["category"] for r in out).most_common()),
        "by_section": dict(Counter(r["section"] for r in out).most_common()),
        "by_year": dict(sorted(Counter(r["event_day"][:4] for r in out).items())),
        "event_day_span": [out[0]["event_day"], out[-1]["event_day"]] if out else None,
        "distinct_event_days": len({r["event_day"] for r in out}),
    }
    return out, summary


FIELDS = ["event_id", "msg_time_export", "msg_time_beijing", "beijing_date",
          "event_day", "section", "category", "n_chars", "text"]


def write(items: list[dict], summary: dict) -> None:
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EVENTS_DIR / "events.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(items)
    (EVENTS_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[extract] {len(items)} 条 -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="微信导出的聊天消息 CSV")
    args = parser.parse_args()
    items, summary = extract(Path(args.csv))
    write(items, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
