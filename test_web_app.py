import copy
import unittest

import web_app


class WebAppLogicTest(unittest.TestCase):
    def setUp(self):
        self.rates = {
            "source": "test",
            "status": "test",
            "fetched_at": "2026-05-12T00:00:00Z",
            "pair_rates": {"USD": 7.2, "EUR": 7.8},
        }

    def test_dashboard_builds_suggestions_and_backtest(self):
        # Pass an empty forecast doc so the test does not depend on whether
        # data/forecast_signals.json happens to exist on disk.
        dashboard = web_app.build_dashboard(web_app.DEMO_STATE, self.rates, forecast_doc={})

        self.assertEqual(len(dashboard["exposures"]), 2)
        self.assertEqual(len(dashboard["hedges"]), 1)
        self.assertEqual(len(dashboard["suggestions"]), 2)
        self.assertGreater(len(dashboard["plain_language"]), 2)

        usd = next(row for row in dashboard["net_exposures"] if row["currency"] == "USD")
        self.assertEqual(usd["business_exposure"], 1200000)
        self.assertEqual(usd["locked_exposure"], -500000)
        self.assertEqual(usd["net_exposure"], 700000)
        self.assertEqual(usd["target_hedge_ratio"], 0.8)

        usd_suggestion = next(row for row in dashboard["suggestions"] if row["currency"] == "USD")
        self.assertEqual(usd_suggestion["recommended_amount"], 460000)
        self.assertIn("neutral", usd_suggestion["scenario_projection"])
        self.assertIn("fair_value_change_gain_loss", usd_suggestion["scenario_projection"]["optimistic"])

        backtest_usd = next(row for row in dashboard["backtest"] if row["currency"] == "USD")
        self.assertEqual(backtest_usd["hedge_effect_cny"], -15000)

    def test_scenario_rows_cover_exposures_without_recommendation(self):
        # 已锁量超过目标覆盖量时不会再产生建议，但剩余敞口的浮动损益必须照样出现。
        state = copy.deepcopy(web_app.DEMO_STATE)
        state["hedges"][0]["amount"] = 1000000
        dashboard = web_app.build_dashboard(state, self.rates, forecast_doc={})

        usd_suggestion = [row for row in dashboard["suggestions"] if row["currency"] == "USD"]
        self.assertEqual(usd_suggestion, [])

        usd_scenario = next(row for row in dashboard["scenario_rows"] if row["currency"] == "USD")
        self.assertFalse(usd_scenario["has_recommendation"])
        self.assertEqual(usd_scenario["recommended_amount"], 0)
        self.assertEqual(usd_scenario["net_exposure"], 200000)
        # 敞口还在，乐观/悲观两端就不能都是 0
        self.assertNotEqual(usd_scenario["projection"]["optimistic"]["total_projected_gain_loss"], 0)
        self.assertIn("2026-06:USD", dashboard["scenario_summary"])

    def test_scenario_rows_match_suggestion_projection(self):
        dashboard = web_app.build_dashboard(web_app.DEMO_STATE, self.rates, forecast_doc={})
        usd_suggestion = next(row for row in dashboard["suggestions"] if row["currency"] == "USD")
        usd_scenario = next(row for row in dashboard["scenario_rows"] if row["currency"] == "USD")

        self.assertTrue(usd_scenario["has_recommendation"])
        self.assertEqual(usd_scenario["projection"], usd_suggestion["scenario_projection"])

    def test_over_risk_limit_is_a_flag_not_a_decision(self):
        state = copy.deepcopy(web_app.DEMO_STATE)
        state["config"] = dict(web_app.DEFAULT_CONFIG, risk_limit_cny=1000000)
        dashboard = web_app.build_dashboard(state, self.rates, forecast_doc={})

        usd = next(row for row in dashboard["net_exposures"] if row["currency"] == "USD")
        self.assertTrue(usd["over_risk_limit"])

        # 阈值只是提示：抬高阈值不改变任何建议金额
        state["config"] = dict(web_app.DEFAULT_CONFIG, risk_limit_cny=10 ** 9)
        relaxed = web_app.build_dashboard(state, self.rates, forecast_doc={})
        relaxed_usd = next(row for row in relaxed["net_exposures"] if row["currency"] == "USD")
        self.assertFalse(relaxed_usd["over_risk_limit"])
        self.assertEqual(
            [row["recommended_amount"] for row in dashboard["suggestions"]],
            [row["recommended_amount"] for row in relaxed["suggestions"]],
        )

    def test_backtest_marks_rows_without_settlement_rate(self):
        dashboard = web_app.build_dashboard(web_app.DEMO_STATE, self.rates, forecast_doc={})

        usd = next(row for row in dashboard["backtest"] if row["currency"] == "USD")
        self.assertTrue(usd["settled"])
        self.assertEqual(usd["rate_basis"], "settlement")
        self.assertEqual(usd["actual_rate"], 7.21)

        eur = next(row for row in dashboard["backtest"] if row["currency"] == "EUR")
        self.assertFalse(eur["settled"])
        self.assertEqual(eur["rate_basis"], "market_estimate")
        self.assertIn("尚未录入到期实际汇率", eur["plain_text"])
        self.assertTrue(
            any("还没录入到期实际汇率" in line for line in dashboard["plain_language"])
        )

    def test_scenario_totals_sum_every_currency_and_period(self):
        dashboard = web_app.build_dashboard(web_app.DEMO_STATE, self.rates, forecast_doc={})
        totals = dashboard["scenario_totals"]
        rows = dashboard["scenario_rows"]

        self.assertEqual(set(totals), {"neutral", "optimistic", "pessimistic", "custom"})
        self.assertEqual(totals["neutral"]["total_projected_gain_loss"], 0)

        for name in totals:
            expected_exposure = sum(
                row["projection"][name]["unrealized_exchange_gain_loss"] for row in rows
            )
            expected_hedge = sum(
                row["projection"][name][row["accounting_bucket"]] for row in rows
            )
            self.assertAlmostEqual(
                totals[name]["unrealized_exchange_gain_loss"], round(expected_exposure, 2), places=2
            )
            self.assertAlmostEqual(totals[name]["hedge_pnl"], round(expected_hedge, 2), places=2)
            self.assertAlmostEqual(
                totals[name]["total_projected_gain_loss"],
                round(expected_exposure + expected_hedge, 2),
                places=2,
            )
            self.assertEqual(
                sum(totals[name]["by_bucket"].values()), totals[name]["hedge_pnl"]
            )

        # 乐观与悲观对称，合计应互为相反数
        self.assertAlmostEqual(
            totals["optimistic"]["total_projected_gain_loss"],
            -totals["pessimistic"]["total_projected_gain_loss"],
            places=2,
        )

    def test_portfolio_totals_do_not_net_across_currencies(self):
        dashboard = web_app.build_dashboard(web_app.DEMO_STATE, self.rates, forecast_doc={})
        portfolio = dashboard["portfolio"]

        # USD 净收 + EUR 净付：取绝对值相加，不能互相抵消
        usd_gross = 1200000 * self.rates["pair_rates"]["USD"]
        eur_gross = 350000 * self.rates["pair_rates"]["EUR"]
        self.assertAlmostEqual(portfolio["gross_exposure_cny"], usd_gross + eur_gross, places=2)
        self.assertGreater(portfolio["gross_exposure_cny"], usd_gross)

        self.assertAlmostEqual(
            portfolio["locked_cny"], 500000 * self.rates["pair_rates"]["USD"], places=2
        )
        self.assertAlmostEqual(
            portfolio["locked_ratio"], portfolio["locked_cny"] / portfolio["gross_exposure_cny"], places=4
        )
        self.assertEqual(portfolio["currency_count"], 2)
        self.assertEqual(portfolio["pending_count"], len(dashboard["suggestions"]))
        self.assertEqual(portfolio["rate_missing"], [])

        by_currency = {row["currency"]: row for row in portfolio["by_currency"]}
        self.assertAlmostEqual(
            by_currency["USD"]["recommended_cny"], 460000 * self.rates["pair_rates"]["USD"], places=2
        )
        self.assertEqual(by_currency["EUR"]["locked_cny"], 0)

    def test_portfolio_skips_currencies_without_a_rate(self):
        state = copy.deepcopy(web_app.DEMO_STATE)
        state["exposures"].append(
            {
                "id": "demo-exp-3",
                "due_date": "2026-07-31",
                "currency": "ZZZ",
                "amount": 999,
                "direction": "receipt",
                "category": "cash_flow",
                "probability": 1,
            }
        )
        dashboard = web_app.build_dashboard(state, self.rates, forecast_doc={})
        portfolio = dashboard["portfolio"]

        self.assertIn("ZZZ", portfolio["rate_missing"])
        zzz = next(row for row in portfolio["by_currency"] if row["currency"] == "ZZZ")
        self.assertEqual(zzz["gross_cny"], 0)

    def test_pair_rates_from_open_endpoint_payload(self):
        payload = {"rates": {"USD": 1, "CNY": 7.2, "EUR": 0.9}}
        rates = web_app.pair_rates_from_payload(payload, ["USD", "EUR"])

        self.assertEqual(rates["USD"], 7.2)
        self.assertEqual(rates["EUR"], 8.0)


if __name__ == "__main__":
    unittest.main()
