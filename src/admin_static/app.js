const tempoGrid = document.getElementById("tempoGrid");
const timeRail = document.getElementById("timeRail");
const marketBoard = document.getElementById("marketBoard");
const runtimeStats = document.getElementById("runtimeStats");
const openPositions = document.getElementById("openPositions");
const closedPositions = document.getElementById("closedPositions");
const testRuns = document.getElementById("testRuns");
const logFeed = document.getElementById("logFeed");
const serviceBadge = document.getElementById("serviceBadge");
const scanForm = document.getElementById("scanForm");
const testForm = document.getElementById("testForm");
const resetForm = document.getElementById("resetForm");
const scanButton = document.getElementById("scanButton");
const testButton = document.getElementById("testButton");
const resetButton = document.getElementById("resetButton");
const statusBanner = document.getElementById("statusBanner");
const heroClockPrimary = document.getElementById("heroClockPrimary");
const heroClockSecondary = document.getElementById("heroClockSecondary");
const heroClockMeta = document.getElementById("heroClockMeta");
const timelineFilters = document.getElementById("timelineFilters");
const marketFilters = document.getElementById("marketFilters");

let currentSnapshot = window.__INITIAL_DASHBOARD__ || null;
let timelineFilter = "all";
let marketFilter = "all";
let reloadTimer = null;

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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function money(value) {
  return currency.format(Number(value || 0));
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatAbsolute(date) {
  if (!date) return "Waiting";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatUtcTime(date) {
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

function formatLocalTime(date) {
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function countdown(target, now = new Date()) {
  if (!target) return "Waiting";
  const rawSeconds = Math.round((target.getTime() - now.getTime()) / 1000);
  const past = rawSeconds < 0;
  const seconds = Math.abs(rawSeconds);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;

  let text = `${remainder}s`;
  if (days > 0) {
    text = `${days}d ${hours}h`;
  } else if (hours > 0) {
    text = `${hours}h ${minutes}m`;
  } else if (minutes > 0) {
    text = `${minutes}m ${String(remainder).padStart(2, "0")}s`;
  }

  return past ? `${text} ago` : `in ${text}`;
}

function sourceClass(source) {
  return source === "gamma" ? "live" : source === "demo" ? "demo" : "neutral";
}

function chip(label, tone = "cool") {
  return `<span class="chip ${tone}">${escapeHtml(label)}</span>`;
}

function logTone(level) {
  if (level === "ERROR") return "danger";
  if (level === "WARNING") return "warn";
  return "cool";
}

function testTone(status) {
  if (status === "failed") return "danger";
  if (status === "passed") return "good";
  return "warn";
}

function setStatus(message, tone = "ok") {
  statusBanner.textContent = message;
  statusBanner.className = `status-banner ${tone}`;
}

function nextScan(snapshot, now = new Date()) {
  const interval = Number(snapshot.service?.scan_interval_seconds || 0);
  const lastScan = parseDate(snapshot.summary?.last_scan_at);
  if (lastScan && interval > 0) {
    return new Date(lastScan.getTime() + interval * 1000);
  }
  return now;
}

function nextMarket(snapshot) {
  const markets = (snapshot.active_markets || [])
    .map((market) => ({ market, date: parseDate(market.end_date) }))
    .filter((item) => item.date)
    .sort((a, b) => a.date - b.date);
  return markets[0] || null;
}

function nextSettlement(snapshot) {
  const positions = (snapshot.open_positions || [])
    .map((position) => ({ position, date: parseDate(position.resolve_at) }))
    .filter((item) => item.date)
    .sort((a, b) => a.date - b.date);
  return positions[0] || null;
}

function latestTest(snapshot) {
  return snapshot.test_runs?.[0] || null;
}

function renderHeroClock(snapshot, now) {
  const nextScanTime = nextScan(snapshot, now);
  const nextMarketTime = nextMarket(snapshot);
  const nextSettleTime = nextSettlement(snapshot);

  heroClockPrimary.textContent = formatLocalTime(now);
  heroClockSecondary.textContent = `${formatUtcTime(now)} UTC`;
  heroClockMeta.textContent = [
    `Next scan ${countdown(nextScanTime, now)}`,
    nextMarketTime ? `Market ${countdown(nextMarketTime.date, now)}` : "Market waiting",
    nextSettleTime ? `Settle ${countdown(nextSettleTime.date, now)}` : "No open settlements",
  ].join(" · ");
}

function renderTempoCards(snapshot, now) {
  const nextScanTime = nextScan(snapshot, now);
  const upcomingMarket = nextMarket(snapshot);
  const upcomingSettlement = nextSettlement(snapshot);
  const lastTest = latestTest(snapshot);
  const cards = [
    {
      kicker: "Next Scan",
      value: countdown(nextScanTime, now),
      meta: formatAbsolute(nextScanTime),
      tone: "cool",
    },
    {
      kicker: "Next Market Close",
      value: upcomingMarket ? countdown(upcomingMarket.date, now) : "Waiting",
      meta: upcomingMarket
        ? `${upcomingMarket.market.asset} · ${upcomingMarket.market.data_source}`
        : "No market windows",
      tone: upcomingMarket ? sourceClass(upcomingMarket.market.data_source) : "warn",
    },
    {
      kicker: "Next Settlement",
      value: upcomingSettlement ? countdown(upcomingSettlement.date, now) : "Idle",
      meta: upcomingSettlement
        ? `${upcomingSettlement.position.asset} · ${money(upcomingSettlement.position.expected_profit)} expected`
        : "No open pairs",
      tone: upcomingSettlement ? "good" : "warn",
    },
    {
      kicker: "Test Pulse",
      value: snapshot.service?.tests_running
        ? "Running"
        : lastTest
          ? String(lastTest.status || "unknown").toUpperCase()
          : "Waiting",
      meta: lastTest
        ? `${lastTest.passed ?? 0}/${lastTest.collected ?? 0} passed · ${formatAbsolute(parseDate(lastTest.timestamp))}`
        : "No recent test run",
      tone: snapshot.service?.tests_running ? "warn" : testTone(lastTest?.status),
    },
    {
      kicker: "Equity",
      value: money(snapshot.summary?.equity),
      meta: `Realized ${money(snapshot.summary?.realized_profit)}`,
      tone: "good",
    },
    {
      kicker: "Cash Free",
      value: money(snapshot.summary?.cash_balance),
      meta: `Exposure ${money(snapshot.summary?.open_exposure)}`,
      tone: "cool",
    },
    {
      kicker: "Open Queue",
      value: String(snapshot.summary?.open_positions ?? 0),
      meta: `${snapshot.summary?.closed_positions ?? 0} settled`,
      tone: Number(snapshot.summary?.open_positions || 0) > 0 ? "good" : "warn",
    },
    {
      kicker: "Scan Count",
      value: String(snapshot.summary?.scan_count ?? 0),
      meta: `Source ${snapshot.summary?.last_data_source || "bootstrap"}`,
      tone: "cool",
    },
  ];

  tempoGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="tempo-card tone-${card.tone}">
          <p class="tempo-kicker">${escapeHtml(card.kicker)}</p>
          <strong>${escapeHtml(card.value)}</strong>
          <span>${escapeHtml(card.meta)}</span>
        </article>
      `,
    )
    .join("");
}

function buildTimelineItems(snapshot, now) {
  const items = [];

  for (const market of snapshot.active_markets || []) {
    const at = parseDate(market.end_date);
    items.push({
      kind: "market",
      phase: at && at >= now ? "upcoming" : "recent",
      tone: sourceClass(market.data_source),
      when: at,
      label: market.data_source === "gamma" ? "Live window" : "Demo window",
      title: `${market.asset} market closes`,
      copy: market.question,
      meta: `${countdown(at, now)} · Edge ${percent.format(Number(market.profit_margin || 0))}`,
    });
  }

  for (const position of snapshot.open_positions || []) {
    const at = parseDate(position.resolve_at);
    items.push({
      kind: "position",
      phase: at && at >= now ? "upcoming" : "recent",
      tone: "good",
      when: at,
      label: "Settlement",
      title: `${position.asset} pair settles`,
      copy: position.slug,
      meta: `${countdown(at, now)} · ${money(position.expected_profit)} expected`,
    });
  }

  for (const position of snapshot.closed_positions || []) {
    const at = parseDate(position.settled_at);
    items.push({
      kind: "settled",
      phase: "recent",
      tone: Number(position.profit || 0) >= 0 ? "good" : "danger",
      when: at,
      label: "Settled",
      title: `${position.asset} pair settled`,
      copy: position.slug,
      meta: `${money(position.profit)} realized · ${formatAbsolute(at)}`,
    });
  }

  for (const item of snapshot.test_runs || []) {
    const at = parseDate(item.timestamp);
    items.push({
      kind: "test",
      phase: "recent",
      tone: testTone(item.status),
      when: at,
      label: "Tests",
      title: `Unit tests ${String(item.status || "unknown")}`,
      copy: `${item.passed ?? 0}/${item.collected ?? 0} passed`,
      meta: `${formatAbsolute(at)} · ${item.duration_seconds ? `${Number(item.duration_seconds).toFixed(2)}s` : "No duration"}`,
    });
  }

  for (const item of snapshot.logs || []) {
    const at = parseDate(item.timestamp);
    const payload = item.payload ? Object.values(item.payload).join(" · ") : "";
    items.push({
      kind: "log",
      phase: "recent",
      tone: logTone(item.level),
      when: at,
      label: item.level || "LOG",
      title: item.message,
      copy: payload || "Operator journal entry",
      meta: formatAbsolute(at),
    });
  }

  const upcoming = items
    .filter((item) => item.phase === "upcoming")
    .sort((a, b) => (a.when || now) - (b.when || now));
  const recent = items
    .filter((item) => item.phase === "recent")
    .sort((a, b) => (b.when || now) - (a.when || now));

  if (timelineFilter === "upcoming") return upcoming.slice(0, 12);
  if (timelineFilter === "recent") return recent.slice(0, 12);
  if (timelineFilter === "tests") {
    return recent.filter((item) => item.kind === "test").slice(0, 12);
  }
  return [...upcoming.slice(0, 8), ...recent.slice(0, 8)];
}

function renderTimeRail(snapshot, now) {
  const items = buildTimelineItems(snapshot, now);
  if (!items.length) {
    timeRail.innerHTML = `<p class="empty">No time events yet.</p>`;
    return;
  }

  timeRail.innerHTML = items
    .map(
      (item) => `
        <article class="rail-item tone-${item.tone}">
          <div class="rail-marker">
            <span class="rail-dot"></span>
          </div>
          <div class="rail-body">
            <div class="rail-topline">
              <strong>${escapeHtml(item.title)}</strong>
              ${chip(item.label, item.tone)}
            </div>
            <p>${escapeHtml(item.copy)}</p>
            <div class="rail-meta">${escapeHtml(item.meta)}</div>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderRuntime(snapshot, now) {
  const nextScanTime = nextScan(snapshot, now);
  const upcomingMarket = nextMarket(snapshot);
  const upcomingSettlement = nextSettlement(snapshot);
  const lastTest = latestTest(snapshot);
  const entries = [
    ["Mode", snapshot.summary?.mode || "paper-only"],
    ["Assets", (snapshot.service?.assets || []).join(", ")],
    ["Scan Interval", `${snapshot.service?.scan_interval_seconds || 0}s`],
    ["Last Scan", snapshot.summary?.last_scan_at ? formatAbsolute(parseDate(snapshot.summary.last_scan_at)) : "Never"],
    ["Next Scan", countdown(nextScanTime, now)],
    ["Next Market", upcomingMarket ? `${upcomingMarket.market.asset} · ${countdown(upcomingMarket.date, now)}` : "Waiting"],
    ["Next Settlement", upcomingSettlement ? `${upcomingSettlement.position.asset} · ${countdown(upcomingSettlement.date, now)}` : "Idle"],
    ["Latest Test", lastTest ? `${String(lastTest.status || "unknown").toUpperCase()} · ${formatAbsolute(parseDate(lastTest.timestamp))}` : "No runs yet"],
    ["Last Error", snapshot.summary?.last_error || "None"],
  ];

  runtimeStats.innerHTML = entries
    .map(
      ([label, value]) => `
        <div>
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value)}</dd>
        </div>
      `,
    )
    .join("");
}

function renderMarkets(snapshot, now) {
  let markets = [...(snapshot.active_markets || [])];
  if (marketFilter === "live") {
    markets = markets.filter((market) => market.data_source === "gamma");
  } else if (marketFilter === "demo") {
    markets = markets.filter((market) => market.data_source === "demo");
  } else if (marketFilter === "edge") {
    markets = markets.filter((market) => Boolean(market.opportunity));
  }

  markets.sort((left, right) => {
    const leftDate = parseDate(left.end_date);
    const rightDate = parseDate(right.end_date);
    const delta = (leftDate || now) - (rightDate || now);
    if (delta !== 0) return delta;
    return Number(right.opportunity) - Number(left.opportunity);
  });

  if (!markets.length) {
    marketBoard.innerHTML = `<p class="empty">No market windows for this filter.</p>`;
    return;
  }

  marketBoard.innerHTML = markets
    .map((market) => {
      const endDate = parseDate(market.end_date);
      const tone = sourceClass(market.data_source);
      return `
        <article class="market-card source-${tone} ${market.opportunity ? "opportunity" : ""}">
          <div class="market-topline">
            <strong>${escapeHtml(market.asset)}</strong>
            ${chip(market.data_source, tone === "live" ? "cool" : "warn")}
          </div>
          <h3>${escapeHtml(market.question)}</h3>
          <p class="market-timer">${countdown(endDate, now)} · ${formatAbsolute(endDate)}</p>
          <dl>
            <div><dt>YES</dt><dd>${Number(market.yes_price || 0).toFixed(3)}</dd></div>
            <div><dt>NO</dt><dd>${Number(market.no_price || 0).toFixed(3)}</dd></div>
            <div><dt>Total</dt><dd>${Number(market.combined_cost || 0).toFixed(3)}</dd></div>
            <div><dt>Edge</dt><dd>${percent.format(Number(market.profit_margin || 0))}</dd></div>
          </dl>
          <div class="market-bottomline">
            <span>Volume ${Number(market.volume || 0).toLocaleString()}</span>
            <span>${market.opportunity ? "Tradeable edge" : "Watch window"}</span>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderPositionCards(container, positions, now, settled = false) {
  const sorted = [...positions].sort((left, right) => {
    const leftDate = parseDate(settled ? left.settled_at : left.resolve_at);
    const rightDate = parseDate(settled ? right.settled_at : right.resolve_at);
    return settled ? (rightDate || now) - (leftDate || now) : (leftDate || now) - (rightDate || now);
  });

  if (!sorted.length) {
    container.innerHTML = `<p class="empty">${settled ? "No settled pairs yet." : "No open settlement queue."}</p>`;
    return;
  }

  container.innerHTML = sorted
    .map((position) => {
      const target = parseDate(settled ? position.settled_at : position.resolve_at);
      const tone = settled
        ? Number(position.profit || 0) >= 0
          ? "good"
          : "danger"
        : "good";
      const amount = settled ? position.profit : position.expected_profit;
      return `
        <article class="mini-card tone-${tone}">
          <div class="mini-topline">
            <strong>${escapeHtml(position.asset)}</strong>
            ${chip(position.data_source || (settled ? "settled" : "queue"), settled ? "cool" : sourceClass(position.data_source))}
          </div>
          <h3>${escapeHtml(position.slug || position.question || "Paper pair")}</h3>
          <p class="mini-copy">${escapeHtml(position.question || "Paper arbitrage position")}</p>
          <dl class="mini-grid">
            <div><dt>Stake</dt><dd>${money(position.cost)}</dd></div>
            <div><dt>${settled ? "Profit" : "Expected"}</dt><dd>${money(amount)}</dd></div>
          </dl>
          <div class="mini-meta">${settled ? "Settled" : "Settle"} ${countdown(target, now)} · ${formatAbsolute(target)}</div>
        </article>
      `;
    })
    .join("");
}

function renderTestRuns(snapshot) {
  const items = snapshot.test_runs || [];
  if (!items.length) {
    testRuns.innerHTML = `<p class="empty">No test runs yet.</p>`;
    return;
  }

  testRuns.innerHTML = items
    .map((item) => `
      <article class="stack-card tone-${testTone(item.status)}">
        <div class="stack-topline">
          <strong>${escapeHtml(String(item.status || "unknown").toUpperCase())}</strong>
          ${chip(item.status || "unknown", testTone(item.status))}
        </div>
        <p class="stack-copy">
          ${escapeHtml(`${item.passed ?? 0}/${item.collected ?? 0} passed · ${formatAbsolute(parseDate(item.timestamp))}`)}
        </p>
        <pre>${escapeHtml((item.stderr || item.stdout || "No console output").slice(0, 700))}</pre>
      </article>
    `)
    .join("");
}

function renderLogs(snapshot) {
  const items = snapshot.logs || [];
  if (!items.length) {
    logFeed.innerHTML = `<p class="empty">No journal entries yet.</p>`;
    return;
  }

  logFeed.innerHTML = items
    .map((item) => `
      <article class="stack-card tone-${logTone(item.level)}">
        <div class="stack-topline">
          <strong>${escapeHtml(item.level || "LOG")}</strong>
          ${chip(item.level || "log", logTone(item.level))}
        </div>
        <p class="stack-copy">${escapeHtml(item.message || "")}</p>
        <pre>${escapeHtml(JSON.stringify(item.payload || {}, null, 2))}</pre>
      </article>
    `)
    .join("");
}

function renderServiceBadge(snapshot, toneOverride = null) {
  let tone = "ok";
  let label = "Live";
  if (snapshot.service?.tests_running) {
    tone = "warn";
    label = "Tests Running";
  } else if (snapshot.summary?.last_error) {
    tone = "danger";
    label = "Degraded";
  }
  if (toneOverride) {
    tone = toneOverride;
    label = toneOverride === "danger" ? "Offline" : label;
  }
  serviceBadge.textContent = label;
  serviceBadge.className = `badge ${tone}`;
}

function renderDynamic(snapshot, now = new Date()) {
  renderHeroClock(snapshot, now);
  renderTempoCards(snapshot, now);
  renderTimeRail(snapshot, now);
  renderRuntime(snapshot, now);
  renderMarkets(snapshot, now);
  renderPositionCards(openPositions, snapshot.open_positions || [], now, false);
  renderServiceBadge(snapshot);
}

function renderStatic(snapshot, now = new Date()) {
  renderPositionCards(closedPositions, snapshot.closed_positions || [], now, true);
  renderTestRuns(snapshot);
  renderLogs(snapshot);
}

function renderAll(snapshot, now = new Date()) {
  renderDynamic(snapshot, now);
  renderStatic(snapshot, now);
}

function activateFilter(container, attribute, value) {
  container.querySelectorAll(`[${attribute}]`).forEach((button) => {
    button.classList.toggle("is-active", button.getAttribute(attribute) === value);
  });
}

function autoReloadDelay(snapshot) {
  const intervalMs = Number(snapshot?.service?.scan_interval_seconds || 15) * 1000;
  return Math.max(intervalMs + 2000, 15000);
}

function scheduleReload(snapshot = currentSnapshot) {
  window.clearTimeout(reloadTimer);
  reloadTimer = window.setTimeout(() => {
    if (document.visibilityState === "hidden") {
      scheduleReload(snapshot);
      return;
    }

    setStatus("Refreshing time screen…", "ok");
    window.location.reload();
  }, autoReloadDelay(snapshot));
}

function bindAction(form, button, pendingMessage, tone = "warn", confirmMessage = null) {
  form.addEventListener("submit", (event) => {
    if (confirmMessage && !window.confirm(confirmMessage)) {
      event.preventDefault();
      return;
    }

    button.disabled = true;
    setStatus(pendingMessage, tone);
  });
}

timelineFilters.querySelectorAll("[data-timeline-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    timelineFilter = button.dataset.timelineFilter;
    activateFilter(timelineFilters, "data-timeline-filter", timelineFilter);
    if (currentSnapshot) renderDynamic(currentSnapshot, new Date());
  });
});

marketFilters.querySelectorAll("[data-market-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    marketFilter = button.dataset.marketFilter;
    activateFilter(marketFilters, "data-market-filter", marketFilter);
    if (currentSnapshot) renderDynamic(currentSnapshot, new Date());
  });
});

bindAction(scanForm, scanButton, "Running manual scan…", "warn");
bindAction(testForm, testButton, "Starting test run…", "warn");
bindAction(
  resetForm,
  resetButton,
  "Resetting fake bankroll…",
  "danger",
  "Reset fake bankroll and clear the position history?",
);

if (currentSnapshot) {
  renderAll(currentSnapshot, new Date());
  scheduleReload(currentSnapshot);
}

window.setInterval(() => {
  if (currentSnapshot) {
    renderDynamic(currentSnapshot, new Date());
  }
}, 1000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    scheduleReload(currentSnapshot);
  }
});
