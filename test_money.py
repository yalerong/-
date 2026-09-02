"""金额取整口径的测试。

重点不是"Decimal 更准"这种空话，而是钉住两个具体行为：
四舍五入而不是银行家舍入；十进制字面值不受二进制表示影响。
"""
import unittest

import money
import web_app


class MoneyRoundingTest(unittest.TestCase):
    def test_half_up_not_bankers_rounding(self):
        # Python 内置 round 是 round-half-even：0.125 -> 0.12、0.135 -> 0.14
        self.assertEqual(round(0.125, 2), 0.12)
        # 财务口径要四舍五入，两个都进位
        self.assertEqual(money.f2(0.125), 0.13)
        self.assertEqual(money.f2("0.135"), 0.14)
        self.assertEqual(money.f2(2.675), 2.68)

    def test_negative_amounts_round_away_from_zero(self):
        self.assertEqual(money.f2("-0.125"), -0.13)
        self.assertEqual(money.f2("-2.675"), -2.68)

    def test_decimal_literal_beats_binary_representation(self):
        self.assertNotEqual(0.1 + 0.2, 0.3)
        self.assertEqual(money.q2(money.D("0.1") + money.D("0.2")), money.q2("0.3"))

    def test_blank_and_none_are_zero(self):
        self.assertEqual(money.D(None), 0)
        self.assertEqual(money.D(""), 0)
        self.assertEqual(money.f2(None), 0.0)

    def test_ratio_rounding_uses_the_same_rule(self):
        self.assertEqual(money.f(0.12345, 4), 0.1235)
        self.assertEqual(money.f(0.5, 0), 1.0)


class DashboardRoundingTest(unittest.TestCase):
    def test_backtest_effect_is_exact_to_the_cent(self):
        rates = {"pair_rates": {"USD": 7.2}, "status": "test", "fetched_at": None, "source": "test"}
        state = {
            "config": dict(web_app.DEFAULT_CONFIG),
            "exposures": [{
                "id": "e1", "due_date": "2026-06-30", "currency": "USD",
                "amount": 1000, "direction": "receipt", "category": "cash_flow", "probability": 1,
            }],
            "hedges": [{
                "id": "h1", "trade_date": "2026-05-12", "due_date": "2026-06-30",
                "currency": "USD", "amount": 1000, "action": "sell_foreign", "locked_rate": 7.1,
            }],
            "settlements": [{
                "id": "s1", "due_date": "2026-06-30", "currency": "USD", "actual_rate": 7.2,
            }],
        }
        dashboard = web_app.build_dashboard(state, rates, forecast_doc={})
        row = next(item for item in dashboard["backtest"] if item["currency"] == "USD")
        # 卖出 1000 USD 锁 7.1，到期 7.2：锁汇相对实际汇率少赚 100.00
        # 浮点直算是 -100.00000000000034，取整口径必须给出干净的 -100.0
        self.assertEqual(row["hedge_effect_cny"], -100.0)


if __name__ == "__main__":
    unittest.main()
