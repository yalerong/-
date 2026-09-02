"""HTTP 层测试。

逻辑层测得很细，接口层原来一条没测——路由、参数校验、错误码、并发写
全靠肉眼。这里起一个真的 FxRiskServer，用真的 HTTP 请求打它，
数据目录指到临时目录，不碰工作区里的真实状态。
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import web_app


def request(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


class HttpApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)

        # 把模块级路径指到临时目录，避免测试写坏真实工作区
        cls._saved = {
            "DATA_DIR": web_app.DATA_DIR,
            "STATE_FILE": web_app.STATE_FILE,
            "RATES_CACHE_FILE": web_app.RATES_CACHE_FILE,
            "AUDIT_LOG_FILE": web_app.AUDIT_LOG_FILE,
            "load_rates": web_app.load_rates,
        }
        web_app.DATA_DIR = tmp
        web_app.STATE_FILE = tmp / "fx_workspace.json"
        web_app.RATES_CACHE_FILE = tmp / "rates_cache.json"
        web_app.AUDIT_LOG_FILE = tmp / "audit_log.jsonl"

        # 测试不许出网：汇率固定
        web_app.load_rates = lambda config, force=False: {
            "source": "test",
            "status": "test",
            "fetched_at": "2026-05-12T00:00:00Z",
            "pair_rates": {"USD": 7.2, "EUR": 7.8},
        }

        cls.server = web_app.FxRiskServer(("127.0.0.1", 0), web_app.FxRiskHandler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        for key, value in cls._saved.items():
            setattr(web_app, key, value)
        cls._tmp.cleanup()

    def setUp(self):
        status, _ = request("POST", f"{self.base}/api/reset-demo", {})
        self.assertEqual(status, 200)

    # ---------- 读 ----------

    def test_state_endpoint_returns_dashboard(self):
        status, data = request("GET", f"{self.base}/api/state")
        self.assertEqual(status, 200)
        for key in ("net_exposures", "suggestions", "portfolio", "scenario_totals", "backtest", "audit"):
            self.assertIn(key, data)

    def test_index_is_served(self):
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])

    def test_unknown_path_is_404(self):
        status, _ = request("GET", f"{self.base}/api/nope")
        self.assertEqual(status, 404)

    # ---------- 写 ----------

    def test_create_exposure_then_delete_it(self):
        status, _ = request("POST", f"{self.base}/api/exposures", {
            "due_date": "2026-09-30",
            "currency": "USD",
            "amount": 1000,
            "direction": "receipt",
            "category": "cash_flow",
        })
        self.assertEqual(status, 200)

        _, data = request("GET", f"{self.base}/api/state")
        created = [row for row in data["exposures"] if row["due_date"] == "2026-09-30"]
        self.assertEqual(len(created), 1)
        record_id = created[0]["id"]

        status, payload = request("DELETE", f"{self.base}/api/exposures/{record_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted"], 1)

    def test_delete_unknown_id_is_404(self):
        status, payload = request("DELETE", f"{self.base}/api/exposures/does-not-exist")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])

    def test_invalid_payloads_are_rejected_with_400(self):
        cases = [
            ("缺字段", {"currency": "USD", "amount": 100, "direction": "receipt"}),
            ("方向非法", {"due_date": "2026-09-30", "currency": "USD", "amount": 100, "direction": "sideways"}),
            ("金额为负", {"due_date": "2026-09-30", "currency": "USD", "amount": -5, "direction": "receipt"}),
            ("类目表外", {"due_date": "2026-09-30", "currency": "USD", "amount": 100,
                          "direction": "receipt", "category": "export_order"}),
            ("概率越界", {"due_date": "2026-09-30", "currency": "USD", "amount": 100,
                          "direction": "receipt", "probability": 1.5}),
            ("概率为零", {"due_date": "2026-09-30", "currency": "USD", "amount": 100,
                          "direction": "receipt", "probability": 0}),
        ]
        for name, payload in cases:
            with self.subTest(name):
                status, body = request("POST", f"{self.base}/api/exposures", payload)
                self.assertEqual(status, 400, f"{name} 应该被拒")
                self.assertIn("error", body)

    def test_bad_json_is_400_not_500(self):
        req = urllib.request.Request(
            f"{self.base}/api/exposures", data=b"{not json", method="POST"
        )
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                self.fail(f"应该 400，实际 {response.status}")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)

    def test_config_update_is_merged_not_replaced(self):
        status, payload = request("POST", f"{self.base}/api/config", {"default_hedge_ratio": 0.6})
        self.assertEqual(status, 200)
        self.assertEqual(payload["config"]["default_hedge_ratio"], 0.6)
        # 只传了一个字段，其他默认值不能被抹掉
        self.assertEqual(payload["config"]["base_currency"], "CNY")
        self.assertIn("supported_currencies", payload["config"])

    # ---------- 审计 ----------

    def test_every_mutation_lands_in_the_audit_log(self):
        request("POST", f"{self.base}/api/exposures", {
            "due_date": "2026-10-31", "currency": "USD", "amount": 2000,
            "direction": "payment", "category": "balance_sheet",
        })
        _, data = request("GET", f"{self.base}/api/state")
        record_id = next(row["id"] for row in data["exposures"] if row["due_date"] == "2026-10-31")
        request("DELETE", f"{self.base}/api/exposures/{record_id}")
        request("POST", f"{self.base}/api/config", {"risk_limit_cny": 12345})

        _, data = request("GET", f"{self.base}/api/state")
        audit = data["audit"]
        actions = [(row["action"], row["collection"]) for row in audit]
        self.assertIn(("create", "exposures"), actions)
        self.assertIn(("delete", "exposures"), actions)
        self.assertIn(("update", "config"), actions)

        # 日志是倒序的，最新一条在最前，且删除记录里留着删掉之前的样子
        deleted = next(row for row in audit if row["action"] == "delete")
        self.assertEqual(deleted["before"]["amount"], 2000)
        self.assertIsNone(deleted["after"])

        config_change = next(row for row in audit if row["collection"] == "config")
        self.assertEqual(config_change["after"]["risk_limit_cny"]["to"], 12345)

    def test_failed_validation_leaves_no_audit_entry(self):
        _, before = request("GET", f"{self.base}/api/state")
        request("POST", f"{self.base}/api/exposures", {"currency": "USD"})
        _, after = request("GET", f"{self.base}/api/state")
        self.assertEqual(len(after["audit"]), len(before["audit"]))

    # ---------- 并发 ----------

    def test_concurrent_writes_do_not_lose_records(self):
        # 状态是"整份读出来改完再整份写回"，没有锁就会互相覆盖。
        count = 12
        errors: list[Exception] = []

        def post(index: int) -> None:
            try:
                status, _ = request("POST", f"{self.base}/api/exposures", {
                    "due_date": "2026-11-30",
                    "currency": "USD",
                    "amount": 100 + index,
                    "direction": "receipt",
                    "category": "cash_flow",
                })
                if status != 200:
                    errors.append(RuntimeError(f"status={status}"))
            except Exception as exc:  # pragma: no cover - 出错就让断言暴露
                errors.append(exc)

        threads = [threading.Thread(target=post, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(errors, [])
        _, data = request("GET", f"{self.base}/api/state")
        created = [row for row in data["exposures"] if row["due_date"] == "2026-11-30"]
        self.assertEqual(len(created), count, "并发写丢了记录")
        self.assertEqual(len({row["id"] for row in created}), count, "id 撞了")


if __name__ == "__main__":
    unittest.main()
