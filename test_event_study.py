"""事件研究的方向规则与统计口径测试。规则见 docs/event-driven-plan.md 第 5、6、8 节。"""
from __future__ import annotations

import unittest

from events.event_study import (
    baseline_direction,
    binom_p_one_sided,
    direction_for,
    event_direction,
    hit,
    passes_surprise,
    score,
    two_prop_p,
)


def f(d2=0.0, dxy=0.0, dvix=0.0, trend20=0.0):
    return {"d2": d2, "dxy": dxy, "dvix": dvix, "trend20": trend20}


class DirectionRuleTest(unittest.TestCase):
    def test_us_needs_both_proxies_same_sign(self):
        self.assertEqual(direction_for("US_MON", f(d2=0.05, dxy=0.003)), "up")
        self.assertEqual(direction_for("US_DATA", f(d2=-0.05, dxy=-0.003)), "down")

    def test_us_conflict_gives_no_direction(self):
        # 两个意外代理符号冲突 -> 不强行解释
        self.assertIsNone(direction_for("US_MON", f(d2=0.05, dxy=-0.003)))
        self.assertIsNone(direction_for("US_DATA", f(d2=-0.05, dxy=0.003)))

    def test_cn_keyed_on_dollar_only(self):
        self.assertEqual(direction_for("CN_POL", f(dxy=0.003)), "up")
        self.assertEqual(direction_for("CN_DATA", f(dxy=-0.003)), "down")
        self.assertIsNone(direction_for("CN_POL", f(dxy=0.0)))

    def test_trade_needs_dollar_and_vix_agree(self):
        self.assertEqual(direction_for("TRADE", f(dxy=0.003, dvix=1.0)), "up")
        self.assertEqual(direction_for("TRADE", f(dxy=-0.003, dvix=-1.0)), "down")
        self.assertIsNone(direction_for("TRADE", f(dxy=0.003, dvix=-1.0)))

    def test_fx_pol_is_counter_trend(self):
        self.assertEqual(direction_for("FX_POL", f(trend20=0.01)), "down")
        self.assertEqual(direction_for("FX_POL", f(trend20=-0.01)), "up")
        self.assertIsNone(direction_for("FX_POL", f(trend20=0.0)))


class AggregationTest(unittest.TestCase):
    def test_agreeing_categories_kept(self):
        self.assertEqual(event_direction({"US_MON", "CN_POL"}, f(d2=0.05, dxy=0.003)), "up")

    def test_conflicting_categories_drop_the_day(self):
        # US 无方向、CN 给 down、FX_POL 逆趋势给 up -> 冲突 -> 整日剔除
        state = f(d2=0.05, dxy=-0.003, trend20=-0.01)
        self.assertEqual(direction_for("CN_POL", state), "down")
        self.assertEqual(direction_for("FX_POL", state), "up")
        self.assertIsNone(event_direction({"CN_POL", "FX_POL"}, state))

    def test_all_none_drops_the_day(self):
        self.assertIsNone(event_direction({"US_MON"}, f(d2=0.05, dxy=-0.003)))


class SurpriseGateTest(unittest.TestCase):
    def test_main_tier_thresholds(self):
        self.assertTrue(passes_surprise(f(d2=0.03)))
        self.assertTrue(passes_surprise(f(dxy=0.002)))
        self.assertFalse(passes_surprise(f(d2=0.02, dxy=0.001)))

    def test_strict_tier_is_stricter(self):
        state = f(d2=0.05, dxy=0.003)
        self.assertTrue(passes_surprise(state))
        self.assertFalse(passes_surprise(state, strict=True))


class BaselineTest(unittest.TestCase):
    def test_b1_requires_agreement(self):
        self.assertEqual(baseline_direction(f(d2=0.05, dxy=0.003), "B1"), "up")
        self.assertIsNone(baseline_direction(f(d2=0.05, dxy=-0.003), "B1"))

    def test_b2_is_dollar_sign_only(self):
        self.assertEqual(baseline_direction(f(d2=-0.05, dxy=0.003), "B2"), "up")


class StatsTest(unittest.TestCase):
    def test_hit_and_zero_return_excluded(self):
        self.assertTrue(hit("up", 0.001))
        self.assertFalse(hit("up", -0.001))
        self.assertIsNone(hit("up", 0.0))

    def test_score_skips_zero_returns(self):
        s = score([("up", 0.001), ("up", 0.0), ("down", 0.001)])
        self.assertEqual(s["n"], 2)
        self.assertEqual(s["hits"], 1)

    def test_binomial_matches_known_values(self):
        self.assertAlmostEqual(binom_p_one_sided(10, 10), 1 / 1024)
        self.assertAlmostEqual(binom_p_one_sided(0, 10), 1.0)

    def test_two_prop_identical_rates_is_half(self):
        self.assertAlmostEqual(two_prop_p(50, 100, 50, 100), 0.5, places=6)

    def test_two_prop_detects_higher_rate(self):
        self.assertLess(two_prop_p(70, 100, 50, 100), 0.05)


if __name__ == "__main__":
    unittest.main()
