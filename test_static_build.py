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
BAKED_RE = r"var BAKED = (\{.*?\});\n"


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
        match = re.search(BAKED_RE, self.html, re.S)
        self.assertIsNotNone(match, "找不到烘进去的 dashboard")
        baked = json.loads(match.group(1))
        for key in ("net_exposures", "suggestions", "portfolio", "scenario_totals",
                    "scenario_rows", "backtest", "audit", "plans", "plan_drift", "config"):
            self.assertIn(key, baked, f"烘进去的数据缺 {key}")
        self.assertTrue(baked["suggestions"], "演示数据要有待锁汇建议，否则页面空一块")

    def test_demo_data_shows_the_things_worth_showing(self):
        match = re.search(BAKED_RE, self.html, re.S)
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
        # 断言 "只读演示" 是不够的：改写后的 <title> 里也有这四个字，
        # 横幅整个丢掉这条也照样绿。要断言横幅特有的标记。
        self.assertIn('class="demo-banner"', self.html, "只读横幅没插进去")
        self.assertIn("数据全部合成", self.html)
        self.assertIn('name="robots"', self.html)
        self.assertIn("Disallow", (self.out / "robots.txt").read_text(encoding="utf-8"))

    def test_banner_says_when_the_data_was_baked(self):
        # 页面把构建那天的日期烘死在里面（到期日、已过期标记都是相对它算的）。
        # 不写明截至日期的话，放久了访客看到的是一份过期决策而不自知。
        import datetime
        self.assertIn("数据截至", self.html)
        self.assertIn(datetime.date.today().isoformat(), self.html)

    def test_forecast_signals_track_the_build_date(self):
        # 不能用签入的那份固定月份信号：演示期间是从构建日往后推的，
        # 两者错开就会拿一段已经结束的预测去影响别的到期日
        match = re.search(BAKED_RE, self.html, re.S)
        baked = json.loads(match.group(1))
        signals = (baked.get("forecast") or {}).get("signals") or {}
        self.assertTrue(signals, "演示站要有信号，否则闸门那块看不到")
        import datetime
        today = datetime.date.today()
        for currency, signal in signals.items():
            with self.subTest(currency=currency):
                months = [row["month"] for row in signal.get("forecast", [])]
                self.assertTrue(months)
                self.assertGreaterEqual(max(months), today.strftime("%Y-%m"),
                                        "信号的预测区间不能整段落在过去")

    def test_refuses_to_wipe_source_directories(self):
        """构建前会 rmtree 输出目录，所以危险参数必须在删之前就被拒。

        `python demo/build_static.py .` 会解析成仓库根、`demo` 会解析成
        脚本自己所在的目录——这两个参数都挺自然，不能靠使用者小心。
        """
        for arg in (".", "demo", "web", "data"):
            with self.subTest(arg=arg):
                proc = subprocess.run(
                    [sys.executable, str(BUILD), arg],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                )
                self.assertNotEqual(proc.returncode, 0, f"传 {arg} 居然构建成功了")
                self.assertIn("拒绝", proc.stdout + proc.stderr)
        # 源文件一个都不能少
        for name in ("demo/build_static.py", "demo/index.html", "web/index.html", "web_app.py"):
            self.assertTrue((ROOT / name).exists(), f"{name} 被删了")

    def test_no_external_attribution_leaks_into_the_bundle(self):
        for path in self.out.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for word in ("酷滴", "114463", "fx_patent_reconstruction"):
                self.assertNotIn(word, text, f"{path.name} 里有不该外发的表述：{word}")


if __name__ == "__main__":
    unittest.main()
