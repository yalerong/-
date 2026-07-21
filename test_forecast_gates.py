import math
import unittest

from forecast import pipeline, trend_gate


def bt(**overrides):
    base = {
        "mape": 0.02,
        "direction_accuracy": 0.65,
        "n_test": 40,
        "direction_hits": 26,  # 26/40, binomial p ~ 0.04
        "interval_coverage": 0.8,
    }
    base.update(overrides)
    return base


class ClassifyTierTest(unittest.TestCase):
    def test_missing_metrics_reject(self):
        tier, reasons = pipeline.classify_tier({"mape": None, "direction_accuracy": 0.6})
        self.assertEqual(tier, "reject")
        self.assertTrue(reasons)

    def test_good_metrics_support(self):
        tier, reasons = pipeline.classify_tier(bt())
        self.assertEqual(tier, "support")
        self.assertEqual(reasons, [])

    def test_small_sample_demotes_support(self):
        tier, reasons = pipeline.classify_tier(bt(n_test=12, direction_hits=9))
        self.assertEqual(tier, "caution")
        self.assertIn("样本不足", reasons[0])

    def test_insignificant_direction_demotes_support(self):
        # 22/40 = 55% 达到阈值但 p ~ 0.32，统计上与抛硬币无异
        tier, reasons = pipeline.classify_tier(bt(direction_accuracy=0.55, direction_hits=22))
        self.assertEqual(tier, "caution")
        self.assertIn("显著", reasons[0])

    def test_low_coverage_demotes_support(self):
        tier, reasons = pipeline.classify_tier(bt(interval_coverage=0.5))
        self.assertEqual(tier, "caution")
        self.assertIn("区间覆盖率", reasons[0])

    def test_very_low_coverage_rejects(self):
        # 直接进 caution 档（MAPE 偏高），覆盖率 23% 应把它杀到 reject
        tier, reasons = pipeline.classify_tier(
            bt(mape=0.04, direction_accuracy=0.52, interval_coverage=0.23)
        )
        self.assertEqual(tier, "reject")
        self.assertIn("失真", reasons[-1])

    def test_missing_coverage_caps_at_caution(self):
        tier, reasons = pipeline.classify_tier(bt(interval_coverage=None))
        self.assertEqual(tier, "caution")

    def test_binom_p_sanity(self):
        self.assertAlmostEqual(pipeline.binom_p_one_sided(0, 40), 1.0)
        self.assertLess(pipeline.binom_p_one_sided(40, 40), 1e-9)
        # 命中一半时 p 略高于 0.5
        self.assertGreater(pipeline.binom_p_one_sided(20, 40), 0.5)


class TrendGateTest(unittest.TestCase):
    def _series(self, drift):
        # 单调漂移 + 小幅锯齿，确保 ATR 非零
        return [100 + drift * i + (0.05 if i % 2 else -0.05) for i in range(120)]

    def test_uptrend(self):
        out = trend_gate.evaluate_series(self._series(+0.3))
        self.assertEqual(out["direction"], "up")
        self.assertEqual(out["alignment"], 6)
        self.assertGreater(out["energy"], 0)

    def test_downtrend(self):
        out = trend_gate.evaluate_series(self._series(-0.3))
        self.assertEqual(out["direction"], "down")
        self.assertEqual(out["alignment"], 6)
        self.assertLess(out["energy"], 0)

    def test_too_short_raises(self):
        with self.assertRaises(RuntimeError):
            trend_gate.evaluate_series([100.0] * 30)


if __name__ == "__main__":
    unittest.main()
