"""静态演示站构建的冒烟测试。

`demo/build_static.py` 靠几个字符串锚点往 `web/index.html` 里插垫片。
锚点一变（比如改了 app.js 的版本号写法），垫片就**静默**不注入——
本地跑构建照样成功，部署上去才发现页面在打真实的 /api 然后白屏。
这里把这几件事钉死。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "demo" / "build_static.py"


class StaticBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "site"
        proc = subprocess.run(
            [sys.executable, str(BUILD), str(cls.out)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        cls.html = (cls.out / "index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_file_the_page_needs_is_there(self):
        for name in ("index.html", "method.html", "robots.txt", "404.html",
                     "web/app.js", "web/styles.css"):
            self.assertTrue((self.out / name).exists(), f"缺 {name}")

    def test_the_shim_actually_got_injected_before_app_js(self):
        self.assertIn("静态演示垫片", self.html, "垫片没注入——锚点大概改了")
        shim_at = self.html.index("静态演示垫片")
        app_at = self.html.index('src="/web/app.js')
        self.assertLess(shim_at, app_at, "垫片必须排在 app.js 之前，否则接管不到 fetch")

    def test_baked_state_is_valid_json_with_the_keys_the_page_reads(self):
        match = re.search(r"var BAKED = (\{.*?\});\n", self.html, re.S)
        self.assertIsNotNone(match, "找不到烘进去的 dashboard")
        baked = json.loads(match.group(1))
        for key in ("net_exposures", "suggestions", "portfolio", "scenario_totals",
                    "scenario_rows", "backtest", "audit", "plans", "plan_drift", "config"):
            self.assertIn(key, baked, f"烘进去的数据缺 {key}")
        self.assertTrue(baked["suggestions"], "演示数据要有待锁汇建议，否则页面空一块")

    def test_demo_data_shows_the_things_worth_showing(self):
        match = re.search(r"var BAKED = (\{.*?\});\n", self.html, re.S)
        baked = json.loads(match.group(1))

        # 有未来期间，远期贴水才看得见、中性情景才不为 0
        bases = {row.get("forward_basis") for row in baked["suggestions"]}
        self.assertIn("cip", bases, "演示数据要有未来期间，否则远期价全退回即期")
        self.assertNotEqual(baked["scenario_totals"]["neutral"]["total_projected_gain_loss"], 0)

        # 有已结算且录了实际发生额的期间，价量分解才出得来
        with_variance = [
            row for row in baked["backtest"]
            if (row.get("benchmark") or {}).get("variance")
        ]
        self.assertTrue(with_variance, "演示数据要有一条录了实际发生额的结算")

        # 冻过方案且改过参数，方案存档与漂移提示才是活的
        self.assertTrue(baked["plans"])
        self.assertTrue(baked["plan_drift"]["stale"], "演示站要能看到参数漂移的样子")

    def test_page_is_marked_read_only_and_not_indexed(self):
        self.assertIn("只读演示", self.html)
        self.assertIn('name="robots"', self.html)
        self.assertIn("Disallow", (self.out / "robots.txt").read_text(encoding="utf-8"))

    def test_no_external_attribution_leaks_into_the_bundle(self):
        for path in self.out.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for word in ("酷滴", "114463", "fx_patent_reconstruction"):
                self.assertNotIn(word, text, f"{path.name} 里有不该外发的表述：{word}")


if __name__ == "__main__":
    unittest.main()
