"""境内定价序列（中间价 / CNH）的写盘与量级自检测试。口径见 events/cn_fetch.py 模块文档。"""
from __future__ import annotations

import csv
import unittest
from unittest import mock

from events import cn_fetch


class WriteSeriesTest(unittest.TestCase):
    def test_dedup_and_sort(self):
        with mock.patch.object(cn_fetch, "MARKET_DIR", cn_fetch.ROOT / "data" / "tmp"):
            path = cn_fetch.write_series("_TEST_SERIES", [
                ("2026-01-03", 7.1), ("2026-01-02", 7.0), ("2026-01-03", 7.1)])
            rows = list(csv.DictReader(path.open(encoding="utf-8")))
            path.unlink()
        self.assertEqual([r["date"] for r in rows], ["2026-01-02", "2026-01-03"])


class SanityCheckTest(unittest.TestCase):
    @staticmethod
    def _series(onshore, fixing, cnh):
        def load(sid):
            return {"DEXCHUS": onshore, "CNY_FIXING": fixing, "USDCNH": cnh}[sid]
        return load

    def test_normal_deviations_pass(self):
        onshore = [("2026-01-02", 7.10), ("2026-01-03", 7.12)]
        fixing = [("2026-01-02", 7.09), ("2026-01-03", 7.11)]
        cnh = [("2026-01-02", 7.11), ("2026-01-03", 7.13)]
        with mock.patch("events.market_fetch.load_series", self._series(onshore, fixing, cnh)):
            out = cn_fetch.sanity_check()
        self.assertEqual(out["common_days"], 2)
        self.assertLess(abs(out["spot_vs_fixing_mean_pct"]), 1.0)

    def test_wrong_series_raises(self):
        # 中间价拿成了别的币种，偏离远超 5%
        onshore = [("2026-01-02", 7.10)]
        fixing = [("2026-01-02", 1.08)]
        cnh = [("2026-01-02", 7.11)]
        with mock.patch("events.market_fetch.load_series", self._series(onshore, fixing, cnh)):
            with self.assertRaises(RuntimeError):
                cn_fetch.sanity_check()

    def test_no_common_days_raises(self):
        with mock.patch("events.market_fetch.load_series",
                        self._series([("2026-01-02", 7.1)], [("2025-01-02", 7.1)],
                                     [("2024-01-02", 7.1)])):
            with self.assertRaises(RuntimeError):
                cn_fetch.sanity_check()


class RealDataBandTest(unittest.TestCase):
    """真实数据的硬约束：即期对中间价的偏离受 ±2% 日浮动区间限制。"""

    def test_deviation_within_regulatory_band(self):
        try:
            out = cn_fetch.sanity_check()
        except FileNotFoundError:
            self.skipTest("尚未拉取境内序列，先跑 python -m events.cn_fetch")
        self.assertLess(out["spot_vs_fixing_abs_max_pct"], 2.5)
        self.assertGreater(out["common_days"], 1000)


if __name__ == "__main__":
    unittest.main()
