"""类目推导规则的两侧对拍。

推荐类目的规则在 `web_app.suggest_category`（后端）和 `web/app.js`
的 `suggestCategory`（表单实时提示）各写了一遍——表单要在用户还没提交
时就给出提示，不适合每敲一下就打一次接口。

两处各写一遍就有漂移风险，所以照 `demo/parity_check.py` 的老办法：
从 app.js 里按标记抽出函数丢进 Node 跑，和 Python 版逐格比。

没有 node 就跳过（CI 的两条作业都有 node）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import web_app

APP_JS = Path(__file__).resolve().parent / "web" / "app.js"
BEGIN = "// ---- category:begin ----"
END = "// ---- category:end ----"

CASES = [
    {"booked": True, "probability": 1},
    {"booked": True, "probability": 0.5},
    {"booked": False, "probability": 1},
    {"booked": False, "probability": 0.99},
    {"booked": False, "probability": 0.5},
    {"booked": False, "probability": 0.01},
    {"booked": False, "probability": None},
    {"probability": 1},
    {},
]


def normalize(reason: str) -> str:
    return re.sub(r"\d+%", "<pct>", reason)


@unittest.skipUnless(shutil.which("node"), "需要 node 才能跑 JS 那一侧")
class CategoryParityTest(unittest.TestCase):
    def test_both_sides_agree(self):
        text = APP_JS.read_text(encoding="utf-8")
        body = text[text.index(BEGIN):text.index(END)]

        script = (
            body
            + "\nconst cases = " + json.dumps(CASES) + ";"
            + "\nconsole.log(JSON.stringify(cases.map(suggestCategory)));\n"
        )
        proc = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        js_results = json.loads(proc.stdout)

        for case, (js_category, js_reason) in zip(CASES, js_results):
            with self.subTest(case=case):
                py_category, py_reason = web_app.suggest_category(case)
                self.assertEqual(py_category, js_category)
                self.assertEqual(normalize(py_reason), normalize(js_reason))

    def test_python_side_covers_every_branch(self):
        # 对拍只能保证两边一样，保证不了两边都对；分支本身也要钉住
        self.assertEqual(web_app.suggest_category({"booked": True})[0], "balance_sheet")
        self.assertEqual(web_app.suggest_category({"probability": 1})[0], "order_contract")
        self.assertEqual(web_app.suggest_category({"probability": 0.9})[0], "cash_flow")


if __name__ == "__main__":
    unittest.main()
