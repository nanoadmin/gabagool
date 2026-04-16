const summaryGrid = document.getElementById("summaryGrid");
const marketBoard = document.getElementById("marketBoard");
const runtimeStats = document.getElementById("runtimeStats");
const openPositions = document.getElementById("openPositions");
const closedPositions = document.getElementById("closedPositions");
const testRuns = document.getElementById("testRuns");
const logFeed = document.getElementById("logFeed");
const serviceBadge = document.getElementById("serviceBadge");
const scanButton = document.getElementById("scanButton");
const testButton = document.getElementById("testButton");
const resetButton = document.getElementById("resetButton");

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
});

const percent = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed: ${response.status}`);
  }
  return response.json();
}

function renderSummary(summary) {
  const cards = [
    ["Cash Balance", currency.format(summary.cash_balance)],
    ["Equity", currency.format(summary.equity)],
    ["Open Profit", currency.format(summary.open_profit)],
    ["Realized Profit", currency.format(summary.realized_profit)],
    ["Open Positions", String(summary.open_positions)],
    ["Success Rate", percent.format(summary.success_rate)],
  ];

  summaryGrid.innerHTML = cards
    .map(
      ([label, value]) => `
        <article class="summary-card">
          <p>${label}</p>
          <strong>${value}</strong>
        </article>
      `
    )
    .join("");
}

function renderMarkets(markets) {
  if (!markets.length) {
    marketBoard.innerHTML = `<p class="empty">No market data yet.</p>`;
    return;
  }

  marketBoard.innerHTML = markets
    .map(
      (market) => `
        <article class="market-card ${market.opportunity ? "opportunity" : ""}">
          <div class="market-topline">
            <span>${market.asset}</span>
            <span>${market.data_source}</span>
          </div>
          <h3>${market.question}</h3>
          <dl>
            <div><dt>YES</dt><dd>${market.yes_price.toFixed(3)}</dd></div>
            <div><dt>NO</dt><dd>${market.no_price.toFixed(3)}</dd></div>
            <div><dt>Total</dt><dd>${market.combined_cost.toFixed(3)}</dd></div>
            <div><dt>Edge</dt><dd>${percent.format(market.profit_margin)}</dd></div>
          </dl>
          <p class="market-meta">Resolves ${new Date(market.end_date).toLocaleString()}</p>
        </article>
      `
    )
    .join("");
}

function renderRuntime(snapshot) {
  const service = snapshot.service;
  const summary = snapshot.summary;
  const entries = [
    ["Mode", summary.mode],
    ["Banner", summary.banner],
    ["Assets", service.assets.join(", ")],
    ["Scan Interval", `${service.scan_interval_seconds}s`],
    ["Last Scan", summary.last_scan_at ? new Date(summary.last_scan_at).toLocaleString() : "Never"],
    ["Last Source", summary.last_data_source],
    ["Scans", String(summary.scan_count)],
    ["Last Error", summary.last_error || "None"],
  ];

  serviceBadge.textContent = service.tests_running ? "Tests Running" : "Healthy";
  serviceBadge.className = `badge ${service.tests_running ? "warn" : "ok"}`;
  runtimeStats.innerHTML = entries
    .map(
      ([label, value]) => `
        <div>
          <dt>${label}</dt>
          <dd>${value}</dd>
        </div>
      `
    )
    .join("");
}

function renderPositions(container, positions, settled = false) {
  if (!positions.length) {
    container.innerHTML = `<p class="empty">${settled ? "No settled trades yet." : "No active positions."}</p>`;
    return;
  }

  const rows = positions
    .map(
      (position) => `
        <tr>
          <td>${position.asset}</td>
          <td>${position.slug}</td>
          <td>${currency.format(position.cost)}</td>
          <td>${currency.format(settled ? position.profit : position.expected_profit)}</td>
          <td>${new Date(settled ? position.settled_at : position.resolve_at).toLocaleString()}</td>
        </tr>
      `
    )
    .join("");

  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Asset</th>
          <th>Market</th>
          <th>Stake</th>
          <th>${settled ? "Profit" : "Expected"}</th>
          <th>${settled ? "Settled" : "Resolve At"}</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderTestRuns(items) {
  if (!items.length) {
    testRuns.innerHTML = `<p class="empty">No test runs yet.</p>`;
    return;
  }

  testRuns.innerHTML = items
    .map(
      (item) => `
        <article class="stack-card">
          <div class="stack-topline">
            <strong>${item.status.toUpperCase()}</strong>
            <span>${new Date(item.timestamp).toLocaleString()}</span>
          </div>
          <p>Passed ${item.passed ?? 0}, Failed ${item.failed ?? 0}, Collected ${item.collected ?? 0}</p>
          <pre>${(item.stderr || item.stdout || "No console output").slice(0, 800)}</pre>
        </article>
      `
    )
    .join("");
}

function renderLogs(items) {
  if (!items.length) {
    logFeed.innerHTML = `<p class="empty">No logs yet.</p>`;
    return;
  }

  logFeed.innerHTML = items
    .map(
      (item) => `
        <article class="stack-card">
          <div class="stack-topline">
            <strong>${item.level}</strong>
            <span>${new Date(item.timestamp).toLocaleString()}</span>
          </div>
          <p>${item.message}</p>
          <pre>${JSON.stringify(item.payload || {}, null, 2)}</pre>
        </article>
      `
    )
    .join("");
}

function render(snapshot) {
  renderSummary(snapshot.summary);
  renderMarkets(snapshot.active_markets);
  renderRuntime(snapshot);
  renderPositions(openPositions, snapshot.open_positions, false);
  renderPositions(closedPositions, snapshot.closed_positions, true);
  renderTestRuns(snapshot.test_runs);
  renderLogs(snapshot.logs);
}

async function refresh() {
  try {
    const snapshot = await request("/api/dashboard");
    render(snapshot);
  } catch (error) {
    serviceBadge.textContent = "Offline";
    serviceBadge.className = "badge danger";
    console.error(error);
  }
}

scanButton.addEventListener("click", async () => {
  scanButton.disabled = true;
  try {
    const snapshot = await request("/api/actions/scan", { method: "POST" });
    render(snapshot);
  } finally {
    scanButton.disabled = false;
  }
});

testButton.addEventListener("click", async () => {
  testButton.disabled = true;
  try {
    await request("/api/actions/tests", { method: "POST" });
    await refresh();
  } catch (error) {
    console.error(error);
  } finally {
    testButton.disabled = false;
  }
});

resetButton.addEventListener("click", async () => {
  if (!window.confirm("Reset fake bankroll and clear position history?")) {
    return;
  }
  resetButton.disabled = true;
  try {
    const snapshot = await request("/api/actions/reset", { method: "POST" });
    render(snapshot);
  } finally {
    resetButton.disabled = false;
  }
});

refresh();
window.setInterval(refresh, 10000);
