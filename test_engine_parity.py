"""两套实现的对拍。

仓库里有两条独立的敞口计算路径：

- `web_app.py`：网页工作台，状态是 `data/fx_workspace.json`，独有预测折扣闸门；
- `fx_risk_simulator.py`：离线 CLI，输入是 case JSON，独有远期价、坏账率、
  情景对账校验（`validate_case`）。

两边各写了一遍"按方向定符号 → 按期间和币种汇总"。谁改了一边而没改另一边，
数字就会悄悄分叉。这里用同一份敞口喂给两条路径，钉住聚合结果必须一致。

这不是要把两套合并——它们的定位不同；这是给"允许存在两份实现"这个决定
配一道闸门，和 `demo/parity_check.py` 是同一个思路。
"""
from __future__ import annotations

import unittest

import fx_risk_simulator as sim
import web_app


# 一份敞口，两种表达
EXPOSURES = [
    {"due_date": "2026-06-30", "currency": "USD", "amount": 1200000, "direction": "receipt", "probability": 1},
    {"due_date": "2026-06-15", "currency": "USD", "amount": 300000, "direction": "payment", "probability": 1},
    {"due_date": "2026-06-30", "currency": "EUR", "amount": 350000, "direction": "payment", "probability": 1},
    {"due_date": "2026-07-31", "currency": "USD", "amount": 500000, "direction": "receipt", "probability": 0.5},
]

# simulator 的输入是 case JSON，业务单据走 business_docs
CASE = {
    "rates": {"base": {"USD/CNY": 7.2, "EUR/CNY": 7.8}},
    "business_docs": [
        {"id": "d1", "settlement_period": "2026-06", "currency": "USD",
         "amount": 1200000, "direction": "receipt", "probability": 1},
        {"id": "d2", "settlement_period": "2026-06", "currency": "USD",
         "amount": 300000, "direction": "payment", "probability": 1},
        {"id": "d3", "settlement_period": "2026-06", "currency": "EUR",
         "amount": 350000, "direction": "payment", "probability": 1},
        {"id": "d4", "settlement_period": "2026-07", "currency": "USD",
         "amount": 500000, "direction": "receipt", "probability": 0.5},
    ],
}


class EngineParityTest(unittest.TestCase):
    def test_period_currency_aggregation_matches(self):
        web_totals = web_app.aggregate_rows(EXPOSURES, web_app.signed_exposure)

        sim_exposures = sim.identify_and_measure(CASE)
        sim_totals = sim.aggregate_exposures(sim_exposures)

        self.assertEqual(set(web_totals), set(sim_totals), "期间/币种的键不一致")
        for key in web_totals:
            self.assertAlmostEqual(
                web_totals[key], sim_totals[key], places=6,
                msg=f"{key} 两边算出来的净敞口不同",
            )

    def test_probability_is_applied_on_both_sides(self):
        # 2026-07 那笔概率 0.5，两边都必须按期望金额计
        web_totals = web_app.aggregate_rows(EXPOSURES, web_app.signed_exposure)
        sim_totals = sim.aggregate_exposures(sim.identify_and_measure(CASE))
        self.assertAlmostEqual(web_totals[("2026-07", "USD")], 250000, places=6)
        self.assertAlmostEqual(sim_totals[("2026-07", "USD")], 250000, places=6)

    def test_direction_signs_agree(self):
        # 收为正、付为负，这个约定两边必须相同，否则净额会反号
        self.assertGreater(web_app.signed_exposure(EXPOSURES[0]), 0)
        self.assertLess(web_app.signed_exposure(EXPOSURES[2]), 0)
        self.assertGreater(sim.signed_amount("receipt", 100), 0)
        self.assertLess(sim.signed_amount("payment", 100), 0)


if __name__ == "__main__":
    unittest.main()
