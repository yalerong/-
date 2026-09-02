const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
let dashboard = null;

function showStatus(message, type = "ok") {
  const box = document.getElementById("statusBar");
  box.textContent = message;
  box.className = `status-bar ${type === "ok" ? "" : type}`;
}

function money(value) {
  return fmt.format(Number(value || 0));
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

function setDefaultDates() {
  document.querySelectorAll('input[type="date"]').forEach((input) => {
    if (!input.value) input.value = today();
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed: ${path}`);
  }
  return data;
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const numericKeys = [
    "amount",
    "probability",
    "locked_rate",
    "actual_rate",
    "rate_cache_hours",
    "risk_limit_cny",
    "default_hedge_ratio",
    "optimistic_shift_pct",
    "pessimistic_shift_pct",
    "custom_scenario_shift_pct",
  ];
  for (const key of numericKeys) {
    if (data[key] !== undefined && data[key] !== "") data[key] = Number(data[key]);
  }
  if (data.currency) data.currency = data.currency.toUpperCase();
  return data;
}

async function loadDashboard() {
  dashboard = await api("/api/state");
  renderDashboard(dashboard);
}

function renderDashboard(data) {
  renderRateStatus(data);
  renderPortfolio(data.portfolio || {});
  renderSuggestions(data.suggestions || []);
  renderNetExposure(data.net_exposures || []);
  renderExposureTable(data.exposures || []);
  renderHedgeTable(data.hedges || []);
  renderScenarioRows(data.scenario_rows || [], data.scenario_totals || {});
  renderList("backtestRows", data.backtest || [], renderBacktest);
  renderAudit(data.audit || []);
  renderConfig(data.config || {});
}

function rateStatusText(status) {
  return {
    live: "实时",
    fallback: "内置兜底",
    cached_after_refresh_error: "刷新失败·用缓存",
  }[status] || status || "-";
}

function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

function ratioBar(ratio) {
  const pct = Math.max(0, Math.min(1, Number(ratio || 0))) * 100;
  return `<span class="bar"><span class="bar-fill" style="width:${pct.toFixed(1)}%"></span></span>`;
}

function kpiCard(label, value, note, tone) {
  return `
    <div class="kpi${tone ? " kpi-" + tone : ""}">
      <span class="kpi-label">${label}</span>
      <strong class="kpi-value">${value}</strong>
      <span class="kpi-note">${note || ""}</span>
    </div>
  `;
}

function renderPortfolio(portfolio) {
  const box = document.getElementById("portfolioCards");
  const scope = document.getElementById("portfolioScope");
  const tbody = document.getElementById("portfolioByCurrency");
  const note = document.getElementById("portfolioNote");
  const badge = document.getElementById("todoCount");
  if (!box) return;

  const rows = portfolio.by_currency || [];
  if (!rows.length) {
    box.innerHTML = '<div class="kpi"><span class="kpi-label">暂无敞口</span><strong class="kpi-value">—</strong><span class="kpi-note">先在「录入」里添加一笔</span></div>';
    tbody.innerHTML = "";
    scope.textContent = "";
    note.textContent = "";
    if (badge) badge.textContent = "";
    return;
  }

  scope.textContent = `${portfolio.currency_count} 个币种 · ${portfolio.period_count} 个期间 · ${portfolio.leg_count} 组敞口`;
  box.innerHTML = [
    kpiCard("业务敞口合计", money(portfolio.gross_exposure_cny) + " CNY", "各币种取绝对值后相加"),
    kpiCard("已锁合计", money(portfolio.locked_cny) + " CNY", `已锁比例 ${ratioText(portfolio.locked_ratio)}`),
    kpiCard("剩余敞口", money(portfolio.net_exposure_cny) + " CNY", "扣掉已锁之后仍暴露的部分", "warn"),
    kpiCard("待锁建议", money(portfolio.recommended_cny) + " CNY", `${portfolio.pending_count} 条建议待处理`, "todo"),
  ].join("");

  tbody.innerHTML = rows.map((row) => `
    <tr>
      <td>${row.currency}</td>
      <td>${money(row.gross_cny)}</td>
      <td>${money(row.locked_cny)}</td>
      <td>${money(row.net_cny)}</td>
      <td>${ratioBar(row.locked_ratio)} ${ratioText(row.locked_ratio)}</td>
      <td>${money(row.recommended_cny)}</td>
    </tr>
  `).join("");

  const missing = portfolio.rate_missing || [];
  note.textContent = missing.length
    ? `各币种取绝对值后相加，不同币种不互相抵消。${missing.join("、")} 暂无汇率，未计入合计。`
    : "各币种取绝对值后相加，不同币种不互相抵消——净收美元和净付欧元是两个独立的风险。";

  if (badge) badge.textContent = portfolio.pending_count ? String(portfolio.pending_count) : "";
}

function setupSideNav() {
  const links = Array.from(document.querySelectorAll(".sidenav a[data-nav]"));
  if (!links.length) return;
  const sections = links
    .map((link) => document.getElementById(link.dataset.nav))
    .filter(Boolean);

  const activate = (id) => {
    links.forEach((link) => {
      link.classList.toggle("active", link.dataset.nav === id);
    });
  };

  if ("IntersectionObserver" in window) {
    const seen = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => seen.set(entry.target.id, entry));
        const visible = Array.from(seen.values())
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length) activate(visible[0].target.id);
      },
      { rootMargin: "-72px 0px -55% 0px", threshold: 0 },
    );
    sections.forEach((section) => observer.observe(section));
  }

  links.forEach((link) => {
    link.addEventListener("click", () => activate(link.dataset.nav));
  });
  activate(links[0].dataset.nav);
}

function renderRateStatus(data) {
  const rates = data.rates || {};
  document.getElementById("rateStatus").textContent =
    `汇率：${rateStatusText(rates.status)} · 更新于 ${fmtTime(rates.fetched_at)}`;
}

function renderSuggestions(items) {
  const box = document.getElementById("suggestions");
  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = '<div class="card">暂无建议。先添加敞口。</div>';
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "card";
    const ratioLine = item.effective_hedge_ratio !== undefined && item.effective_hedge_ratio !== item.target_hedge_ratio
      ? `${ratioText(item.target_hedge_ratio)} → 实际 ${ratioText(item.effective_hedge_ratio)}`
      : ratioText(item.target_hedge_ratio);
    card.innerHTML = `
      <strong>${item.period} ${item.currency}</strong>
      <p>${item.plain_text}</p>
      ${renderForecastBlock(item)}
      <p class="meta">剩余敞口：${money(item.net_exposure)}，目标套保比例：${ratioLine}，损益科目：${bucketName(item.accounting_bucket)}</p>
      <p class="meta">建议金额：${money(item.recommended_amount)}，交易汇率：${item.trade_rate}，人民币风险：${money(item.risk_cny)}</p>
      <button type="button">按建议填入锁汇单</button>
    `;
    card.querySelector("button").addEventListener("click", () => fillHedgeFromSuggestion(item));
    box.appendChild(card);
  });
}

function renderForecastBlock(item) {
  const s = item.forecast_signal;
  if (!s) return "";
  const tier = s.tier || "reject";
  const dir = s.direction || "flat";
  const chips = [];
  chips.push(`<span class="chip dir-${dir}">${dirText(dir)}</span>`);
  if (s.mape !== null && s.mape !== undefined) {
    chips.push(`<span class="chip">MAPE ${(s.mape * 100).toFixed(1)}%</span>`);
  }
  if (s.direction_accuracy !== null && s.direction_accuracy !== undefined) {
    chips.push(`<span class="chip">方向准确 ${(s.direction_accuracy * 100).toFixed(0)}%</span>`);
  }
  if (s.interval_coverage !== null && s.interval_coverage !== undefined) {
    chips.push(`<span class="chip">区间覆盖 ${(s.interval_coverage * 100).toFixed(0)}%</span>`);
  }
  if (s.trend && (s.trend.direction === "up" || s.trend.direction === "down")) {
    const arrow = s.trend.direction === "up" ? "↑" : "↓";
    chips.push(`<span class="chip">势能 ${arrow} ${s.trend.alignment}/6</span>`);
  }
  chips.push(`<span class="chip tier-${tier}">${tierText(tier)}</span>`);
  const notes = [];
  if (item.forecast_reason) notes.push(item.forecast_reason);
  if (s.tier_reasons && s.tier_reasons.length) notes.push(...s.tier_reasons);
  const reason = notes.length ? `<p class="meta forecast-reason">${notes.join("；")}</p>` : "";
  return `
    <div class="forecast-block">
      <div class="forecast-chips">${chips.join("")}</div>
      ${sparkline(s.forecast, s.current)}
      ${reason}
    </div>
  `;
}

function dirText(dir) {
  return { up: "预测 ↑", down: "预测 ↓", flat: "预测 →" }[dir] || dir;
}

function tierText(tier) {
  return { support: "模型支持", caution: "模型谨慎", reject: "模型不达标" }[tier] || tier;
}

function sparkline(points, currentRate) {
  if (!points || !points.length) return "";
  const vals = (currentRate != null ? [Number(currentRate)] : []).concat(points.map((p) => Number(p.rate)));
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const range = max - min || 1;
  const w = 180;
  const h = 38;
  const stepX = vals.length > 1 ? w / (vals.length - 1) : w;
  const yAt = (v) => (h - ((v - min) / range) * (h - 4)) - 2;
  const path = vals.map((v, i) => `${i === 0 ? "M" : "L"}${(i * stepX).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");
  return `<svg class="forecast-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <path d="${path}" fill="none" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="0" cy="${yAt(vals[0]).toFixed(1)}" r="2.4" fill="currentColor"/>
  </svg>`;
}

function fillHedgeFromSuggestion(item) {
  const form = document.getElementById("hedgeForm");
  form.trade_date.value = today();
  form.due_date.value = `${item.period}-28`;
  form.currency.value = item.currency;
  form.action.value = item.action;
  form.amount.value = item.recommended_amount;
  form.locked_rate.value = item.trade_rate || item.current_rate;
  form.description.value = item.plain_text;
  form.scrollIntoView({ behavior: "smooth", block: "center" });
  showStatus("已按建议填入锁汇单，请确认后保存。");
}

function renderNetExposure(rows) {
  const body = document.getElementById("netExposureRows");
  body.innerHTML = "";
  rows.forEach((row) => {
    const net = Number(row.net_exposure);
    const side = net > 0 ? { label: "净收", cls: "in" } : net < 0 ? { label: "净付", cls: "out" } : { label: "持平", cls: "flat" };
    const rateCell = row.rate_available
      ? `${row.current_rate}`
      : '<span class="warn-tag" title="该币种暂无汇率，人民币金额无法估算">汇率缺失</span>';
    const riskCell = row.rate_available
      ? `${money(row.cny_risk)}${row.over_risk_limit ? ' <span class="warn-tag" title="超过配置的风险阈值，仅作提示">超阈值</span>' : ""}`
      : "—";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.period}</td>
      <td>${row.currency}</td>
      <td>${riskCategoryCell(row.risk_category, row.risk_category_known)}</td>
      <td>${ratioText(row.target_hedge_ratio)}</td>
      <td>${money(row.business_exposure)}</td>
      <td>${money(row.locked_exposure)}</td>
      <td><span class="net-tag net-${side.cls}">${side.label}</span> ${money(Math.abs(net))}</td>
      <td>${rateCell}</td>
      <td>${riskCell}</td>
    `;
    body.appendChild(tr);
  });
}

function renderScenarioTotals(totals, legCount) {
  const names = ["neutral", "optimistic", "pessimistic", "custom"].filter((n) => totals[n]);
  if (!names.length) return "";
  const rows = names.map((name) => {
    const row = totals[name];
    return `
      <tr>
        <td>${scenarioName(name)}</td>
        <td>${money(row.unrealized_exchange_gain_loss)}</td>
        <td>${money(row.hedge_pnl)}</td>
        <td class="total-cell">${money(row.total_projected_gain_loss)}</td>
      </tr>
    `;
  }).join("");
  const best = totals.optimistic && totals.pessimistic
    ? Math.abs(totals.optimistic.total_projected_gain_loss - totals.pessimistic.total_projected_gain_loss)
    : null;
  const spread = best === null ? "" : `乐观与悲观两端相差 ${money(best)} CNY。`;
  return `
    <div class="item total-item">
      <strong>全部敞口合计（${legCount} 组期间 × 币种）</strong>
      <p class="meta">各币种损益已折成人民币，可直接相加；不同到期日按名义金额相加，未做贴现。${spread}</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>场景</th>
              <th>未实现汇兑损益</th>
              <th>套保损益</th>
              <th>合计预计损益</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}

function renderScenarioRows(entries, totals) {
  const box = document.getElementById("scenarioRows");
  box.innerHTML = "";
  if (!entries.length) {
    box.innerHTML = '<div class="item">暂无敞口，因此没有预计损益场景。</div>';
    return;
  }
  // 先给组合层面的总账，再给逐个期间/币种的明细。
  box.insertAdjacentHTML("beforeend", renderScenarioTotals(totals || {}, entries.length));
  entries.forEach((item) => {
    const rows = Object.entries(item.projection || {}).map(([name, row]) => {
      const bucketValue = row[item.accounting_bucket] || 0;
      return `
        <tr>
          <td>${scenarioName(name)}</td>
          <td>${row.scenario_rate}</td>
          <td>${money(row.unrealized_exchange_gain_loss)}</td>
          <td>${money(bucketValue)}</td>
          <td>${money(row.total_projected_gain_loss)}</td>
        </tr>
      `;
    }).join("");
    const div = document.createElement("div");
    div.className = "item";
    // 建议金额为 0 时套保腿为 0，但敞口本身的浮动损益依然要显示。
    const title = item.has_recommendation
      ? `${item.period} ${item.currency} 推荐交易预计损益`
      : `${item.period} ${item.currency} 当前敞口预计损益`;
    const note = item.has_recommendation
      ? `建议金额 ${money(item.recommended_amount)}。`
      : "无新增建议（已达目标套保比例），下表只反映剩余敞口本身的浮动损益。";
    div.innerHTML = `
      <strong>${title}</strong>
      <p class="meta">${note}科目：${bucketName(item.accounting_bucket)}。中性、乐观、悲观、自定义场景按配置的汇率涨跌幅计算。</p>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>场景</th>
              <th>情景汇率</th>
              <th>未实现汇兑损益</th>
              <th>${bucketName(item.accounting_bucket)}</th>
              <th>合计预计损益</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
    box.appendChild(div);
  });
}

function renderList(id, rows, renderItem) {
  const box = document.getElementById(id);
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = '<div class="item">暂无数据。</div>';
    return;
  }
  rows.forEach((row) => box.appendChild(renderItem(row)));
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function byDueDate(a, b) {
  const left = `${a.due_date || ""}${a.currency || ""}`;
  const right = `${b.due_date || ""}${b.currency || ""}`;
  return left.localeCompare(right);
}

// 明细用表格：字段各占一列，能横向比对，不再把信息塞进句子里。
function renderDetailTable(tbodyId, countId, rows, columns, collection, emptyText) {
  const body = document.getElementById(tbodyId);
  const count = document.getElementById(countId);
  if (!body) return;
  if (count) count.textContent = rows.length ? `共 ${rows.length} 条` : "";
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${columns.length + 1}" class="empty-row">${emptyText}</td></tr>`;
    return;
  }
  body.innerHTML = "";
  rows.slice().sort(byDueDate).forEach((row) => {
    const tr = document.createElement("tr");
    const cells = columns.map((col) => {
      const value = col.render(row);
      return `<td${col.cls ? ` class="${col.cls}"` : ""}>${value}</td>`;
    }).join("");
    tr.innerHTML = `${cells}<td class="col-action"><button type="button" class="row-delete">删除</button></td>`;
    const title = `${row.due_date || ""} ${row.currency || ""} ${money(row.amount)}`;
    tr.querySelector("button").addEventListener("click", async () => {
      if (!window.confirm(`确认删除这条记录？此操作无法撤销。\n${title}`)) return;
      await runAction("正在删除...", async () => {
        await api(`/api/${collection}/${row.id}`, { method: "DELETE" });
        await loadDashboard();
        showStatus("已删除。");
      });
    });
    body.appendChild(tr);
  });
}

function renderExposureTable(rows) {
  renderDetailTable("exposureRows", "exposureCount", rows, [
    { render: (row) => escapeHtml(row.due_date) },
    { render: (row) => escapeHtml(row.currency) },
    {
      render: (row) => row.direction === "receipt"
        ? '<span class="net-tag net-in">收</span> 未来收外币'
        : '<span class="net-tag net-out">付</span> 未来付外币',
    },
    { render: (row) => money(row.amount), cls: "num" },
    { render: (row) => ratioText(row.probability == null ? 1 : row.probability), cls: "num" },
    { render: (row) => riskCategoryCell(row.category) },
    { render: (row) => escapeHtml(row.description || "—"), cls: "col-note" },
  ], "exposures", "还没有敞口，先到「录入」里添加一笔。");
}

function renderHedgeTable(rows) {
  renderDetailTable("hedgeRows", "hedgeCount", rows, [
    { render: (row) => escapeHtml(row.trade_date) },
    { render: (row) => escapeHtml(row.due_date) },
    { render: (row) => escapeHtml(row.currency) },
    {
      render: (row) => row.action === "sell_foreign"
        ? "卖出外币/远期结汇"
        : "买入外币/远期购汇",
    },
    { render: (row) => money(row.amount), cls: "num" },
    { render: (row) => escapeHtml(row.locked_rate), cls: "num" },
    { render: (row) => escapeHtml(row.description || "—"), cls: "col-note" },
  ], "hedges", "还没有锁汇记录。");
}

function renderBacktest(row) {
  const div = document.createElement("div");
  div.className = "item";
  const cls = row.hedge_effect_cny >= 0 ? "positive" : "negative";
  const settled = row.settled !== false;
  const tag = settled ? "" : ' <span class="warn-tag" title="尚未录入到期实际汇率，按当前市场价试算">试算</span>';
  const rateLine = settled
    ? `实际汇率 ${row.actual_rate}，参考汇率 ${row.reference_rate}`
    : `未录入实际汇率，按当前市场价 ${row.reference_rate} 试算`;
  div.innerHTML = `
    <strong>${row.period} ${row.currency}</strong>${tag}
    <p>${row.plain_text}</p>
    <p>锁汇贡献${settled ? "" : "（试算）"}：<span class="${cls}">${money(row.hedge_effect_cny)} CNY</span></p>
    <p class="meta">业务敞口 ${money(row.business_exposure)}，${rateLine}</p>
  `;
  return div;
}

const AUDIT_ACTIONS = {
  create: "新增",
  delete: "删除",
  update: "修改",
  reset: "恢复样例",
};

const AUDIT_COLLECTIONS = {
  exposures: "敞口",
  hedges: "锁汇",
  settlements: "到期汇率",
  config: "配置",
  workspace: "整个工作区",
};

function auditSummary(row) {
  if (row.collection === "config") {
    const changes = Object.entries(row.after || {})
      .map(([key, value]) => `${escapeHtml(key)}：${escapeHtml(value.from)} → ${escapeHtml(value.to)}`);
    return changes.length ? changes.join("；") : "无实际变化";
  }
  if (row.collection === "workspace") return "工作区被重置为样例数据";
  const body = row.after || row.before || {};
  const parts = [body.due_date, body.currency, body.amount == null ? null : money(body.amount)]
    .filter(Boolean)
    .map(escapeHtml);
  const note = body.description ? `（${escapeHtml(body.description)}）` : "";
  return parts.join(" ") + note;
}

function renderAudit(rows) {
  const body = document.getElementById("auditRows");
  const scope = document.getElementById("auditScope");
  if (!body) return;
  if (scope) scope.textContent = rows.length ? `最近 ${rows.length} 条` : "";
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty-row">还没有任何改动。</td></tr>';
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(fmtTime(row.at))}</td>
      <td>${escapeHtml(AUDIT_ACTIONS[row.action] || row.action)}</td>
      <td>${escapeHtml(AUDIT_COLLECTIONS[row.collection] || row.collection)}</td>
      <td class="col-note" title="${escapeHtml(auditSummary(row).replace(/<[^>]*>/g, ""))}">${auditSummary(row)}</td>
    </tr>
  `).join("");
}

function renderConfig(config) {
  const form = document.getElementById("configForm");
  form.rate_api_url.value = config.rate_api_url || "";
  form.rate_cache_hours.value = config.rate_cache_hours || 24;
  form.risk_limit_cny.value = config.risk_limit_cny || 200000;
  form.enterprise_type.value = config.enterprise_type || "comprehensive";
  form.default_hedge_ratio.value = config.default_hedge_ratio ?? 0.8;
  form.optimistic_shift_pct.value = config.optimistic_shift_pct ?? 0.03;
  form.pessimistic_shift_pct.value = config.pessimistic_shift_pct ?? -0.03;
  form.custom_scenario_shift_pct.value = config.custom_scenario_shift_pct ?? 0.01;
}

const RISK_CATEGORY_NAMES = {
  balance_sheet: "资产负债表套保",
  cash_flow: "现金流套保",
  order_contract: "合同/订单套保",
};

function riskCategoryName(value) {
  return RISK_CATEGORY_NAMES[value] || value || "-";
}

// 表外类目是历史数据留下的，会计科目只能兜底到公允价值变动，
// 这里显式标出来而不是装作认识它。
function riskCategoryCell(value, known) {
  const known_ = known === undefined ? value in RISK_CATEGORY_NAMES : known;
  if (known_) return escapeHtml(riskCategoryName(value));
  return `${escapeHtml(value || "-")} <span class="warn-tag" title="不是合法的风险类型，会计科目按公允价值变动兜底；建议删除后重新录入">未知类目</span>`;
}

function bucketName(value) {
  return {
    derivative_investment_income: "衍生品投资收益",
    fair_value_change_gain_loss: "衍生品公允价值变动损益",
    realized_exchange_gain_loss: "已实现汇兑损益",
  }[value] || value || "-";
}

function scenarioName(value) {
  return {
    neutral: "中性",
    optimistic: "乐观",
    pessimistic: "悲观",
    custom: "自定义",
  }[value] || value;
}

function ratioText(value) {
  return `${Math.round(Number(value || 0) * 10000) / 100}%`;
}

function bindForms() {
  document.getElementById("exposureForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.currentTarget, "/api/exposures", "敞口已保存", () => {
      document.getElementById("exposureListPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  document.getElementById("hedgeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.currentTarget, "/api/hedges", "锁汇记录已保存");
  });

  document.getElementById("settlementForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.currentTarget, "/api/settlements", "实际汇率已保存");
  });

  document.getElementById("configForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.currentTarget, "/api/config", "配置已保存", null, false);
  });

  document.getElementById("refreshRatesBtn").addEventListener("click", async () => {
    await runAction("正在刷新汇率...", async () => {
      await api("/api/rates/refresh", { method: "POST", body: "{}" });
      await loadDashboard();
      showStatus("汇率已刷新");
    });
  });

  document.getElementById("resetDemoBtn").addEventListener("click", async () => {
    if (!window.confirm("恢复样例会覆盖当前所有已录入的敞口、锁汇和到期汇率数据，且无法撤销。确认继续？")) return;
    await runAction("正在恢复样例...", async () => {
      await api("/api/reset-demo", { method: "POST", body: "{}" });
      await loadDashboard();
      showStatus("样例数据已恢复");
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener(
      "invalid",
      () => showStatus("有必填项没有填完整，请检查表单。", "error"),
      true,
    );
  });
}

async function submitForm(form, path, successMessage, afterSuccess = null, reset = true) {
  if (!form.reportValidity()) {
    showStatus("有必填项没有填完整，请检查表单。", "error");
    return;
  }
  const button = form.querySelector('button[type="submit"]');
  await runAction("正在保存...", async () => {
    if (button) button.disabled = true;
    await api(path, { method: "POST", body: JSON.stringify(formData(form)) });
    if (reset) {
      form.reset();
      setDefaultDates();
    }
    await loadDashboard();
    showStatus(successMessage);
    if (afterSuccess) afterSuccess();
    if (button) button.disabled = false;
  }, () => {
    if (button) button.disabled = false;
  });
}

async function runAction(busyMessage, action, finallyAction = null) {
  try {
    showStatus(busyMessage, "busy");
    await action();
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    if (finallyAction) finallyAction();
  }
}

bindForms();
setupSideNav();
setDefaultDates();
showStatus("正在加载数据...", "busy");
loadDashboard()
  .then(() => showStatus("数据已就绪。"))
  .catch((error) => {
    showStatus(error.message, "error");
  });
