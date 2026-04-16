#!/usr/bin/env python3
"""
FastAPI app for the Money House paper-trading admin dashboard.
"""

from __future__ import annotations

import logging
import json
from html import escape
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .paper_service import PaperTradingService


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
STATIC_DIR = Path(__file__).parent / "admin_static"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
service = PaperTradingService()


def money(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def percent(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def fmt_ts(value: object) -> str:
    if not value:
        return "Never"
    return escape(str(value))


def render_summary_html(summary: dict) -> str:
    cards = [
        ("Cash Balance", money(summary.get("cash_balance"))),
        ("Equity", money(summary.get("equity"))),
        ("Open Profit", money(summary.get("open_profit"))),
        ("Realized Profit", money(summary.get("realized_profit"))),
        ("Open Positions", escape(str(summary.get("open_positions", 0)))),
        ("Success Rate", percent(summary.get("success_rate"))),
    ]
    return "".join(
        f'<article class="summary-card"><p>{label}</p><strong>{value}</strong></article>'
        for label, value in cards
    )


def render_markets_html(markets: list[dict]) -> str:
    if not markets:
        return '<p class="empty">No market data yet.</p>'

    cards = []
    for market in markets:
        opportunity_class = " opportunity" if market.get("opportunity") else ""
        cards.append(
            (
                f'<article class="market-card{opportunity_class}">'
                f'<div class="market-topline"><span>{escape(str(market.get("asset", "")))}</span>'
                f'<span>{escape(str(market.get("data_source", "")))}</span></div>'
                f'<h3>{escape(str(market.get("question", "")))}</h3>'
                f'<dl>'
                f'<div><dt>YES</dt><dd>{float(market.get("yes_price", 0)):.3f}</dd></div>'
                f'<div><dt>NO</dt><dd>{float(market.get("no_price", 0)):.3f}</dd></div>'
                f'<div><dt>Total</dt><dd>{float(market.get("combined_cost", 0)):.3f}</dd></div>'
                f'<div><dt>Edge</dt><dd>{percent(market.get("profit_margin"))}</dd></div>'
                f'</dl>'
                f'<p class="market-meta">Resolves {fmt_ts(market.get("end_date"))}</p>'
                f'</article>'
            )
        )
    return "".join(cards)


def render_runtime_html(snapshot: dict) -> str:
    service_state = snapshot.get("service", {})
    summary = snapshot.get("summary", {})
    entries = [
        ("Mode", summary.get("mode", "unknown")),
        ("Banner", summary.get("banner", "")),
        ("Assets", ", ".join(service_state.get("assets", []))),
        ("Scan Interval", f"{service_state.get('scan_interval_seconds', 0)}s"),
        ("Last Scan", fmt_ts(summary.get("last_scan_at"))),
        ("Last Source", summary.get("last_data_source", "unknown")),
        ("Scans", str(summary.get("scan_count", 0))),
        ("Last Error", summary.get("last_error") or "None"),
    ]
    return "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>"
        for label, value in entries
    )


def render_positions_html(positions: list[dict], settled: bool = False) -> str:
    if not positions:
        return (
            '<p class="empty">No settled trades yet.</p>'
            if settled
            else '<p class="empty">No active positions.</p>'
        )

    rows = []
    for position in positions:
        rows.append(
            "<tr>"
            f"<td>{escape(str(position.get('asset', '')))}</td>"
            f"<td>{escape(str(position.get('slug', '')))}</td>"
            f"<td>{money(position.get('cost', 0))}</td>"
            f"<td>{money(position.get('profit' if settled else 'expected_profit', 0))}</td>"
            f"<td>{fmt_ts(position.get('settled_at' if settled else 'resolve_at'))}</td>"
            "</tr>"
        )

    last_label = "Settled" if settled else "Resolve At"
    fourth_label = "Profit" if settled else "Expected"
    return (
        "<table><thead><tr>"
        "<th>Asset</th><th>Market</th><th>Stake</th>"
        f"<th>{fourth_label}</th><th>{last_label}</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_test_runs_html(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">No test runs yet.</p>'
    cards = []
    for item in items[:10]:
        cards.append(
            '<article class="stack-card">'
            f'<div class="stack-topline"><strong>{escape(str(item.get("status", "")).upper())}</strong>'
            f'<span>{fmt_ts(item.get("timestamp"))}</span></div>'
            f'<p>Passed {escape(str(item.get("passed") or 0))}, '
            f'Failed {escape(str(item.get("failed") or 0))}, '
            f'Collected {escape(str(item.get("collected") or 0))}</p>'
            f'<pre>{escape(str((item.get("stderr") or item.get("stdout") or "No console output")[:800]))}</pre>'
            "</article>"
        )
    return "".join(cards)


def render_logs_html(items: list[dict]) -> str:
    if not items:
        return '<p class="empty">No logs yet.</p>'
    cards = []
    for item in items[:20]:
        payload = json.dumps(item.get("payload") or {}, indent=2)
        cards.append(
            '<article class="stack-card">'
            f'<div class="stack-topline"><strong>{escape(str(item.get("level", "")))}</strong>'
            f'<span>{fmt_ts(item.get("timestamp"))}</span></div>'
            f'<p>{escape(str(item.get("message", "")))}</p>'
            f'<pre>{escape(payload)}</pre>'
            "</article>"
        )
    return "".join(cards)


def render_index_html(snapshot: dict) -> str:
    template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    service_state = snapshot.get("service", {})
    badge_text = "Tests Running" if service_state.get("tests_running") else "Healthy"
    badge_class = "warn" if service_state.get("tests_running") else "ok"
    initial_json = json.dumps(snapshot).replace("</", "<\\/")
    replacements = {
        "__SERVER_SUMMARY__": render_summary_html(snapshot.get("summary", {})),
        "__SERVER_BADGE_TEXT__": badge_text,
        "__SERVER_BADGE_CLASS__": badge_class,
        "__SERVER_MARKETS__": render_markets_html(snapshot.get("active_markets", [])),
        "__SERVER_RUNTIME__": render_runtime_html(snapshot),
        "__SERVER_OPEN_POSITIONS__": render_positions_html(snapshot.get("open_positions", [])),
        "__SERVER_CLOSED_POSITIONS__": render_positions_html(
            snapshot.get("closed_positions", []), settled=True
        ),
        "__SERVER_TEST_RUNS__": render_test_runs_html(snapshot.get("test_runs", [])),
        "__SERVER_LOGS__": render_logs_html(snapshot.get("logs", [])),
        "__INITIAL_DASHBOARD_JSON__": initial_json,
    }

    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    await service.scan_once()
    try:
        await service.trigger_tests()
    except RuntimeError:
        pass
    yield
    await service.stop()


app = FastAPI(
    title="Money House Admin",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz() -> dict:
    snapshot = service.snapshot()
    return {
        "ok": True,
        "mode": snapshot["summary"]["mode"],
        "last_scan_at": snapshot["summary"]["last_scan_at"],
    }


@app.get("/api/dashboard")
async def dashboard() -> dict:
    return service.snapshot()


@app.post("/api/actions/scan")
async def scan_once() -> dict:
    return await service.scan_once()


@app.post("/api/actions/reset")
async def reset_bankroll() -> dict:
    return await service.reset_bankroll()


@app.post("/api/actions/tests")
async def run_tests() -> dict:
    try:
        return await service.trigger_tests()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.api_route("/", methods=["GET", "HEAD"])
async def index() -> HTMLResponse:
    return HTMLResponse(render_index_html(service.snapshot()))


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg")
