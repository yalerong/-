"""事件日归属与报价日历的口径测试。规则见 docs/event-driven-plan.md 第 3、7 节。"""
from __future__ import annotations

import unittest
from datetime import date

from events.calendar import QuoteCalendar, beijing, to_event_day
from events.market_fetch import parse_fred_csv


class EventDayTest(unittest.TestCase):
    def test_beijing_morning_maps_to_same_day(self):
        # 北京 D 09:00 -> 纽约 D-1 21:00，晚于当日中午 -> 事件日 D
        self.assertEqual(to_event_day(beijing("2026-06-10 09:00:00")), date(2026, 6, 10))

    def test_us_data_release_maps_to_same_day(self):
        # 北京 D 20:30（美国数据常见发布时间）-> 纽约 D 08:30，早于中午 -> 事件日 D
        self.assertEqual(to_event_day(beijing("2026-06-10 20:30:00")), date(2026, 6, 10))

    def test_late_night_still_same_day_in_summer(self):
        # 夏令时时差 12 小时：北京 D 23:00 -> 纽约 D 11:00，仍在中午之前
        self.assertEqual(to_event_day(beijing("2026-06-10 23:00:00")), date(2026, 6, 10))

    def test_after_ny_noon_rolls_to_next_day(self):
        # 北京 D+1 00:30 -> 纽约 D 12:30，晚于中午 -> 事件日 D+1
        self.assertEqual(to_event_day(beijing("2026-06-11 00:30:00")), date(2026, 6, 11))

    def test_winter_offset_pushes_midnight_back(self):
        # 冬令时时差 13 小时：北京 D 00:30 -> 纽约 D-1 11:30，早于中午 -> 事件日 D-1
        self.assertEqual(to_event_day(beijing("2026-01-15 00:30:00")), date(2026, 1, 14))
        # 北京 D 01:30 -> 纽约 D-1 12:30，晚于中午 -> 事件日 D
        self.assertEqual(to_event_day(beijing("2026-01-15 01:30:00")), date(2026, 1, 15))

    def test_naive_datetime_rejected(self):
        from datetime import datetime

        with self.assertRaises(ValueError):
            to_event_day(datetime(2026, 6, 10, 9, 0))

    def test_beijing_rejects_aware_input(self):
        with self.assertRaises(ValueError):
            beijing(beijing("2026-06-10 09:00:00"))


class QuoteCalendarTest(unittest.TestCase):
    def setUp(self):
        # 2026-06-11/12 缺报价，模拟节假日
        self.cal = QuoteCalendar(
            [
                "2026-06-08",
                "2026-06-09",
                "2026-06-10",
                "2026-06-15",
                "2026-06-16",
                "2026-06-17",
                "2026-06-18",
                "2026-06-19",
            ]
        )

    def test_roll_forward_skips_holiday(self):
        self.assertEqual(self.cal.roll_forward(date(2026, 6, 11)), date(2026, 6, 15))
        self.assertEqual(self.cal.roll_forward(date(2026, 6, 10)), date(2026, 6, 10))

    def test_roll_forward_past_end_returns_none(self):
        self.assertIsNone(self.cal.roll_forward(date(2026, 7, 1)))

    def test_shift_counts_quotation_days(self):
        self.assertEqual(self.cal.shift(date(2026, 6, 10), 1), date(2026, 6, 15))
        self.assertEqual(self.cal.shift(date(2026, 6, 10), -1), date(2026, 6, 9))
        self.assertIsNone(self.cal.shift(date(2026, 6, 17), 5))

    def test_window_dates(self):
        w = self.cal.window_dates(date(2026, 6, 10))
        self.assertEqual(w["prev"], date(2026, 6, 9))
        self.assertEqual(w["d"], date(2026, 6, 10))
        self.assertEqual(w["d1"], date(2026, 6, 15))
        self.assertEqual(w["d5"], date(2026, 6, 19))

    def test_window_dates_truncated_at_series_end(self):
        w = self.cal.window_dates(date(2026, 6, 17))
        self.assertEqual(w["d1"], date(2026, 6, 18))
        self.assertIsNone(w["d5"])


class FredParseTest(unittest.TestCase):
    def test_missing_marker_dropped(self):
        text = "observation_date,DEXCHUS\n2026-06-08,7.1000\n2026-06-09,.\n2026-06-10,7.1200\n"
        rows = parse_fred_csv(text)
        self.assertEqual(rows, [("2026-06-08", 7.10), ("2026-06-10", 7.12)])

    def test_bad_header_raises(self):
        with self.assertRaises(RuntimeError):
            parse_fred_csv("garbage\n")


if __name__ == "__main__":
    unittest.main()
