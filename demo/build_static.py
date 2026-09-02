"""把工作台烘成一个可点的静态演示站。

工作台本身是 Python 服务，Cloudflare Pages 只托管静态文件，所以这里：

1. 用一份**合成**的演示数据跑一遍 `build_dashboard`，把结果烘成 JSON；
2. 原样复制真实的 `web/index.html` / `app.js` / `styles.css`——
   演示站跑的就是生产那套前端代码，不是另写一份，否则两边会漂；
3. 在 app.js 之前插一段垫片，把 `fetch("/api/...")` 接管掉：
   读接口返回烘好的 JSON，写接口一律回 403 并说明这是只读演示。

用法：

    python demo/build_static.py <输出目录>
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app  # noqa: E402
import plans  # noqa: E402


def guard_output_dir(out: Path) -> None:
    """构建前会 rmtree 输出目录，所以先确认它不是个能删坏东西的路径。

    `python demo/build_static.py .` 会把 out 解析成仓库根目录，
    直接删掉整个 checkout；传 `demo` 会删掉这个脚本自己和演示页源文件。
    这类参数看着都挺自然，不能靠使用者小心。
    """
    protected = {ROOT, ROOT / "demo", ROOT / "web", ROOT / "data", ROOT / "docs",
                 ROOT / "events", ROOT / "forecast", ROOT / ".git"}
    if out in protected:
        raise SystemExit(f"拒绝：{out} 是源目录，构建会先删空它")
    if out == Path(out.anchor):
        raise SystemExit(f"拒绝：{out} 是盘符/根目录")
    if ROOT == out or ROOT.is_relative_to(out):
        raise SystemExit(f"拒绝：{out} 包含着仓库本身，删它会删掉源码")
    if out.exists() and any(
        (out / name).exists() for name in ("web_app.py", "build_static.py", ".git")
    ):
        raise SystemExit(f"拒绝：{out} 看起来是源目录（里面有源文件），不是构建产物目录")


def month_end(d: date) -> str:
    first_next = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return (first_next - timedelta(days=1)).isoformat()


def demo_state(today: date) -> dict:
    """演示数据。全部合成，刻意排出几种值得看的情形：

    - 一个未来期间：能看到远期贴水，中性情景因此不为 0
    - 一个已结算期间且录了实际发生额：能看到价差/量差分解与超额套保
    - 一个只锁了一半的敞口：能看到覆盖率与待锁建议
    - 一个没有本地行情序列的币种（EUR）：能看到「无月均基准」的诚实兜底
    """
    far = month_end(date(today.year + (today.month > 6), ((today.month + 6 - 1) % 12) + 1, 1))
    near = month_end(date(today.year, today.month, 1) + timedelta(days=62))
    settled = month_end(date(today.year, today.month, 1) - timedelta(days=1))

    return {
        "config": dict(
            web_app.DEFAULT_CONFIG,
            default_hedge_ratio=0.8,
            # 这份数据同时有收和付，只能用 comprehensive。
            # 用 export 的话 action_for 会给净付的欧元敞口也返回 sell_foreign，
            # 于是卡片文案写着"买入外币"、按钮填进去的却是卖出。
            enterprise_type="comprehensive",
            interest_rates={"CNY": 0.019, "USD": 0.043, "EUR": 0.025},
        ),
        "monthly_average_rates": {f"{settled[:7]}:USD": 7.12},
        "exposures": [
            {"id": "d1", "due_date": far, "currency": "USD", "amount": 1200000,
             "direction": "receipt", "category": "order_contract", "probability": 1,
             "booked": False, "description": "出口订单预计收款"},
            {"id": "d2", "due_date": far, "currency": "EUR", "amount": 350000,
             "direction": "payment", "category": "cash_flow", "probability": 0.8,
             "booked": False, "description": "进口采购预计付款"},
            {"id": "d3", "due_date": near, "currency": "USD", "amount": 600000,
             "direction": "receipt", "category": "balance_sheet", "probability": 1,
             "booked": True, "description": "已开票应收"},
            {"id": "d4", "due_date": settled, "currency": "USD", "amount": 900000,
             "direction": "receipt", "category": "order_contract", "probability": 1,
             "booked": False, "description": "上月出口订单"},
        ],
        "hedges": [
            {"id": "h1", "trade_date": settled[:8] + "05", "due_date": far,
             "currency": "USD", "amount": 500000, "action": "sell_foreign",
             "locked_rate": 7.05, "description": "远期结汇锁定部分美元收款"},
            {"id": "h2", "trade_date": settled[:8] + "06", "due_date": settled,
             "currency": "USD", "amount": 700000, "action": "sell_foreign",
             "locked_rate": 7.18, "description": "上月远期结汇"},
        ],
        "settlements": [
            {"id": "s1", "due_date": settled, "currency": "USD",
             "actual_rate": 7.05, "actual_amount": 620000,
             "description": "到期结算，客户只提了一部分货"},
        ],
    }


def demo_forecast(today: date) -> dict:
    """跟着构建日期滚动的合成信号。

    不能用签入的 data/forecast_signals.json：那份是固定月份的，
    而演示敞口的期间是从 today 往后推的，两者会错开——
    拿一段已经结束的预测去影响另一个到期日的建议，演示站上会出现
    自相矛盾的东西。（信号覆盖不到该期间时应该退回 1.0×，
    这个闸门缺口本身另行处理。）
    """
    months = []
    year, month = today.year, today.month
    rate = 7.18
    for _ in range(8):
        month += 1
        if month > 12:
            year, month = year + 1, 1
        rate += 0.012
        months.append({"month": f"{year}-{month:02d}", "rate": round(rate, 4), "dir": "up"})
    return {
        "generated_at": f"{today.isoformat()}T00:00:00Z",
        "horizon_months": len(months),
        "base_currency": "CNY",
        "signals": {
            "USD": {
                "pair": "USDCNY", "current": 7.18,
                "current_month": today.strftime("%Y-%m"),
                "forecast": months, "direction": "up",
                # 预测幅度约 1.3%，刻意压在 MAPE 之下——
                # 演示站默认停在"预测有利但被信噪比闸门挡掉"这一格
                "mape": 0.018, "direction_accuracy": 0.62, "n_test": 30, "tier": "support",
            },
            "EUR": {
                "pair": "EURCNY", "current": 7.72,
                "current_month": today.strftime("%Y-%m"),
                "forecast": [dict(m, rate=round(7.72 + i * 0.02, 4))
                             for i, m in enumerate(months)],
                "direction": "up", "mape": 0.063, "direction_accuracy": 0.42,
                "n_test": 30, "tier": "reject",
            },
        },
    }


DEMO_RATES = {
    "source": "演示用固定汇率（非实时）",
    "source_url": "",
    "status": "demo",
    "fetched_at": None,
    "pair_rates": {"USD": 7.18, "EUR": 7.72, "JPY": 0.049, "HKD": 0.915,
                   "GBP": 9.05, "AUD": 4.72, "SGD": 5.31},
}

SHIM = """<script>
// ==== 静态演示垫片 ====
// 下面这段是**只有演示站有**的东西：把 fetch 接管掉，读接口返回预先烘好的
// dashboard JSON，写接口一律拒绝。前端代码本身与生产版一字不差。
(function () {
  var BAKED = __STATE__;
  function reply(body, status) {
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    }));
  }
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var path = String((input && input.url) || input || "");
    if (path.indexOf("/api/") === -1) return realFetch(input, init);
    var method = ((init && init.method) || "GET").toUpperCase();
    if (method === "GET" && path.indexOf("/api/state") !== -1) return reply(BAKED);
    return reply({
      ok: false,
      error: "这是只读的静态演示站，改不了数据。真实工具在本地运行（见页脚仓库链接）。",
    }, 403);
  };
})();
</script>
"""

BANNER = """<div class="demo-banner">
  <strong>只读演示</strong>
  <span>数据全部合成，不含任何真实企业的头寸或对手方；汇率为演示用固定值。
  按钮和表单可以点，但不会真的改数据。</span>
  <span class="demo-asof">数据截至 __ASOF__</span>
  <a href="/method">看方法与研究结论 →</a>
  <a href="https://github.com/yalerong/FX-Hedge-Lab">源码</a>
</div>
"""

BANNER_CSS = """
.demo-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 18px;
  padding: 12px 24px;
  background: var(--accent-soft);
  border-bottom: 1px solid var(--accent-line);
  font-size: 13px;
  color: var(--ink, var(--text));
}
.demo-banner strong { color: var(--accent-dark); }
.demo-banner span { flex: 1 1 420px; color: var(--muted); }
.demo-banner a { color: var(--accent-dark); font-weight: 600; }
.demo-banner .demo-asof { flex: 0 0 auto; color: var(--muted); font-family: var(--mono, monospace); }
"""


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "demo" / "_static").resolve()
    today = date.today()

    state = demo_state(today)
    forecast = demo_forecast(today)
    dashboard = web_app.build_dashboard(
        state, DEMO_RATES, forecast_doc=forecast, today=today
    )
    # 冻一份方案进去，这样「方案存档」和参数漂移在演示站上也是活的
    plan = dict(plans.freeze(dashboard, "上月冻结的方案", f"{today.isoformat()}T00:00:00Z"),
                id="demo-plan-1")
    state["plans"] = [plan]
    state["config"] = dict(state["config"], default_hedge_ratio=0.7)  # 制造一次参数漂移
    dashboard = web_app.build_dashboard(
        state, DEMO_RATES, forecast_doc=forecast, today=today
    )
    dashboard["audit"] = [
        {"at": f"{today.isoformat()}T02:10:00Z", "action": "update", "collection": "config",
         "id": None, "before": None, "after": {"default_hedge_ratio": {"from": 0.8, "to": 0.7}}},
        {"at": f"{today.isoformat()}T02:05:00Z", "action": "freeze", "collection": "plans",
         "id": "demo-plan-1", "before": None,
         "after": {"label": plan["label"], "rows": len(plan["rows"])}},
        {"at": f"{today.isoformat()}T02:00:00Z", "action": "create", "collection": "exposures",
         "id": "d3", "before": None, "after": state["exposures"][2]},
    ]

    guard_output_dir(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "web").mkdir(parents=True)

    shutil.copy(ROOT / "web" / "app.js", out / "web" / "app.js")
    styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8") + BANNER_CSS
    (out / "web" / "styles.css").write_text(styles, encoding="utf-8")

    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    shim = SHIM.replace("__STATE__", json.dumps(dashboard, ensure_ascii=False))
    banner = BANNER.replace("__ASOF__", today.isoformat())
    html, banner_count = re.subn(r"<body>", "<body>\n" + banner.replace("\\", "\\\\"), html, count=1)
    if banner_count != 1:
        # 只读横幅是这个站唯一说明"数据是合成的"的地方，
        # 静默丢掉比构建失败严重得多
        raise SystemExit("没能插入只读横幅：<body> 锚点变了")
    if '<script src="/web/app.js' not in html:
        raise SystemExit("没能插入垫片：app.js 的引用锚点变了")
    html = html.replace('<script src="/web/app.js', shim + '    <script src="/web/app.js', 1)
    html = html.replace(
        "<title>外汇风险与锁汇工作台</title>",
        "<title>外汇风险与锁汇工作台 · 只读演示</title>"
        '\n    <meta name="robots" content="noindex,nofollow,noarchive" />',
        1,
    )
    (out / "index.html").write_text(html, encoding="utf-8")

    # 原来那个讲方法的展示页留在 /method.html
    shutil.copy(ROOT / "demo" / "index.html", out / "method.html")
    for name in ("robots.txt", "404.html"):
        shutil.copy(ROOT / "demo" / name, out / name)

    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"输出目录: {out}")
    print(f"文件数: {sum(1 for f in out.rglob('*') if f.is_file())}，合计 {size / 1024:.0f} KB")
    print(f"烘入 dashboard: {len(dashboard['suggestions'])} 条建议 / "
          f"{len(dashboard['net_exposures'])} 行净敞口 / {len(dashboard['backtest'])} 行回测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
