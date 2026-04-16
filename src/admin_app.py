#!/usr/bin/env python3
"""
FastAPI app for the Money House paper-trading admin dashboard.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .paper_service import PaperTradingService


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
STATIC_DIR = Path(__file__).parent / "admin_static"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)
service = PaperTradingService()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def money(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def percent_ratio(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def format_abs(value: object) -> str:
    dt = parse_ts(value) if not isinstance(value, datetime) else value
    if not dt:
        return "Waiting"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def human_delta(value: object, now: Optional[datetime] = None) -> str:
    dt = parse_ts(value) if not isinstance(value, datetime) else value
    if not dt:
        return "Waiting"

    current = now or now_utc()
    raw_seconds = int(round((dt - current).total_seconds()))
    past = raw_seconds < 0
    seconds = abs(raw_seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    if days:
        text = f"{days}d {hours}h"
    elif hours:
        text = f"{hours}h {minutes}m"
    elif minutes:
        text = f"{minutes}m {secs:02d}s"
    else:
        text = f"{secs}s"

    return f"{text} ago" if past else f"in {text}"


def source_tone(source: str) -> str:
    if source == "gamma":
        return "cool"
    if source == "demo":
        return "warn"
    return "cool"


def test_tone(status: object) -> str:
    if status == "passed":
        return "good"
    if status == "failed":
        return "danger"
    return "warn"


def log_tone(level: object) -> str:
    if level == "ERROR":
        return "danger"
    if level == "WARNING":
        return "warn"
    return "cool"


def chip(label: str, tone: str) -> str:
    return f'<span class="chip {escape(tone)}">{escape(label)}</span>'


def summarize_payload(payload: object) -> str:
    if not isinstance(payload, dict) or not payload:
        return "Operator journal entry"
    return " · ".join(escape(str(value)) for value in payload.values())


def next_scan(snapshot: dict, now: Optional[datetime] = None) -> datetime:
    current = now or now_utc()
    last_scan = parse_ts(snapshot.get("summary", {}).get("last_scan_at"))
    interval = int(snapshot.get("service", {}).get("scan_interval_seconds", 0) or 0)
    if last_scan and interval:
        return last_scan + timedelta(seconds=interval)
    return current


def next_market(snapshot: dict) -> tuple[Optional[datetime], Optional[dict]]:
    candidates = []
    for market in snapshot.get("active_markets", []):
        dt = parse_ts(market.get("end_date"))
        if dt:
            candidates.append((dt, market))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def next_settlement(snapshot: dict) -> tuple[Optional[datetime], Optional[dict]]:
    candidates = []
    for position in snapshot.get("open_positions", []):
        dt = parse_ts(position.get("resolve_at"))
        if dt:
            candidates.append((dt, position))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def latest_test(snapshot: dict) -> Optional[dict]:
    tests = snapshot.get("test_runs", [])
    return tests[0] if tests else None


def render_tempo_cards_html(snapshot: dict) -> str:
    current = now_utc()
    next_scan_at = next_scan(snapshot, current)
    next_market_at, market = next_market(snapshot)
    next_settle_at, position = next_settlement(snapshot)
    latest = latest_test(snapshot)
    summary = snapshot.get("summary", {})

    cards = [
        {
            "kicker": "Next Scan",
            "value": human_delta(next_scan_at, current),
            "meta": format_abs(next_scan_at),
            "tone": "cool",
        },
        {
            "kicker": "Next Market Close",
            "value": human_delta(next_market_at, current) if next_market_at else "Waiting",
            "meta": f"{market['asset']} · {market['data_source']}" if market else "No market windows",
            "tone": source_tone(str(market.get("data_source"))) if market else "warn",
        },
        {
            "kicker": "Next Settlement",
            "value": human_delta(next_settle_at, current) if next_settle_at else "Idle",
            "meta": (
                f"{position['asset']} · {money(position.get('expected_profit'))} expected"
                if position
                else "No open pairs"
            ),
            "tone": "good" if position else "warn",
        },
        {
            "kicker": "Test Pulse",
            "value": (
                "Running"
                if snapshot.get("service", {}).get("tests_running")
                else str((latest or {}).get("status", "waiting")).upper()
            ),
            "meta": (
                f"{latest.get('passed', 0)}/{latest.get('collected', 0)} passed · {format_abs(latest.get('timestamp'))}"
                if latest
                else "No recent test run"
            ),
            "tone": "warn" if snapshot.get("service", {}).get("tests_running") else test_tone((latest or {}).get("status")),
        },
        {
            "kicker": "Equity",
            "value": money(summary.get("equity")),
            "meta": f"Realized {money(summary.get('realized_profit'))}",
            "tone": "good",
        },
        {
            "kicker": "Cash Free",
            "value": money(summary.get("cash_balance")),
            "meta": f"Exposure {money(summary.get('open_exposure'))}",
            "tone": "cool",
        },
        {
            "kicker": "Open Queue",
            "value": str(summary.get("open_positions", 0)),
            "meta": f"{summary.get('closed_positions', 0)} settled",
            "tone": "good" if summary.get("open_positions", 0) else "warn",
        },
        {
            "kicker": "Scan Count",
            "value": str(summary.get("scan_count", 0)),
            "meta": f"Source {summary.get('last_data_source', 'bootstrap')}",
            "tone": "cool",
        },
    ]

    return "".join(
        (
            f'<article class="tempo-card tone-{escape(card["tone"])}">'
            f'<p class="tempo-kicker">{escape(card["kicker"])}</p>'
            f'<strong>{escape(card["value"])}</strong>'
            f'<span>{escape(card["meta"])}</span>'
            "</article>"
        )
        for card in cards
    )


def build_timeline_items(snapshot: dict) -> list[dict]:
    current = now_utc()
    items: list[dict] = []

    for market in snapshot.get("active_markets", []):
        dt = parse_ts(market.get("end_date"))
        items.append(
            {
                "kind": "market",
                "phase": "upcoming" if dt and dt >= current else "recent",
                "tone": source_tone(str(market.get("data_source"))),
                "when": dt,
                "label": "Live window" if market.get("data_source") == "gamma" else "Demo window",
                "title": f"{market.get('asset', '')} market closes",
                "copy": str(market.get("question", "")),
                "meta": f"{human_delta(dt, current)} · Edge {percent_ratio(market.get('profit_margin'))}",
            }
        )

    for position in snapshot.get("open_positions", []):
        dt = parse_ts(position.get("resolve_at"))
        items.append(
            {
                "kind": "position",
                "phase": "upcoming" if dt and dt >= current else "recent",
                "tone": "good",
                "when": dt,
                "label": "Settlement",
                "title": f"{position.get('asset', '')} pair settles",
                "copy": str(position.get("slug", "")),
                "meta": f"{human_delta(dt, current)} · {money(position.get('expected_profit'))} expected",
            }
        )

    for position in snapshot.get("closed_positions", []):
        dt = parse_ts(position.get("settled_at"))
        items.append(
            {
                "kind": "settled",
                "phase": "recent",
                "tone": "good" if float(position.get("profit", 0) or 0) >= 0 else "danger",
                "when": dt,
                "label": "Settled",
                "title": f"{position.get('asset', '')} pair settled",
                "copy": str(position.get("slug", "")),
                "meta": f"{money(position.get('profit'))} realized · {format_abs(dt)}",
            }
        )

    for item in snapshot.get("test_runs", []):
        dt = parse_ts(item.get("timestamp"))
        items.append(
            {
                "kind": "test",
                "phase": "recent",
                "tone": test_tone(item.get("status")),
                "when": dt,
                "label": "Tests",
                "title": f"Unit tests {item.get('status', 'unknown')}",
                "copy": f"{item.get('passed', 0)}/{item.get('collected', 0)} passed",
                "meta": f"{format_abs(dt)} · {float(item.get('duration_seconds', 0) or 0):.2f}s",
            }
        )

    for item in snapshot.get("logs", []):
        dt = parse_ts(item.get("timestamp"))
        items.append(
            {
                "kind": "log",
                "phase": "recent",
                "tone": log_tone(item.get("level")),
                "when": dt,
                "label": str(item.get("level", "LOG")),
                "title": str(item.get("message", "")),
                "copy": summarize_payload(item.get("payload")),
                "meta": format_abs(dt),
            }
        )

    upcoming = sorted(
        [item for item in items if item["phase"] == "upcoming"],
        key=lambda item: item["when"] or current,
    )[:8]
    recent = sorted(
        [item for item in items if item["phase"] == "recent"],
        key=lambda item: item["when"] or current,
        reverse=True,
    )[:8]
    return upcoming + recent


def render_time_rail_html(snapshot: dict) -> str:
    items = build_timeline_items(snapshot)
    if not items:
        return '<p class="empty">No time events yet.</p>'

    return "".join(
        (
            f'<article class="rail-item tone-{escape(item["tone"])}">'
            '<div class="rail-marker"><span class="rail-dot"></span></div>'
            '<div class="rail-body">'
            f'<div class="rail-topline"><strong>{escape(item["title"])}</strong>{chip(item["label"], item["tone"])}</div>'
            f'<p>{escape(item["copy"])}</p>'
            f'<div class="rail-meta">{escape(item["meta"])}</div>'
            "</div></article>"
        )
        for item in items
    )


def render_runtime_html(snapshot: dict) -> str:
    current = now_utc()
    next_scan_at = next_scan(snapshot, current)
    next_market_at, market = next_market(snapshot)
    next_settle_at, position = next_settlement(snapshot)
    latest = latest_test(snapshot)
    summary = snapshot.get("summary", {})
    service_state = snapshot.get("service", {})

    entries = [
        ("Mode", summary.get("mode", "paper-only")),
        ("Assets", ", ".join(service_state.get("assets", []))),
        ("Scan Interval", f"{service_state.get('scan_interval_seconds', 0)}s"),
        ("Last Scan", format_abs(summary.get("last_scan_at"))),
        ("Next Scan", human_delta(next_scan_at, current)),
        ("Next Market", f"{market['asset']} · {human_delta(next_market_at, current)}" if market else "Waiting"),
        (
            "Next Settlement",
            f"{position['asset']} · {human_delta(next_settle_at, current)}" if position else "Idle",
        ),
        (
            "Latest Test",
            f"{str(latest.get('status', 'unknown')).upper()} · {format_abs(latest.get('timestamp'))}"
            if latest
            else "No runs yet",
        ),
        ("Last Error", summary.get("last_error") or "None"),
    ]

    return "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>"
        for label, value in entries
    )


def render_market_cards_html(markets: Iterable[dict]) -> str:
    market_list = list(markets)
    if not market_list:
        return '<p class="empty">No market windows for this filter.</p>'

    current = now_utc()
    market_list.sort(key=lambda market: parse_ts(market.get("end_date")) or current)

    cards = []
    for market in market_list:
        end_date = parse_ts(market.get("end_date"))
        tone = source_tone(str(market.get("data_source")))
        cards.append(
            (
                f'<article class="market-card source-{escape(tone)}'
                f'{" opportunity" if market.get("opportunity") else ""}">'
                f'<div class="market-topline"><strong>{escape(str(market.get("asset", "")))}</strong>'
                f'{chip(str(market.get("data_source", "")), tone)}</div>'
                f'<h3>{escape(str(market.get("question", "")))}</h3>'
                f'<p class="market-timer">{escape(human_delta(end_date, current))} · {escape(format_abs(end_date))}</p>'
                '<dl>'
                f'<div><dt>YES</dt><dd>{float(market.get("yes_price", 0) or 0):.3f}</dd></div>'
                f'<div><dt>NO</dt><dd>{float(market.get("no_price", 0) or 0):.3f}</dd></div>'
                f'<div><dt>Total</dt><dd>{float(market.get("combined_cost", 0) or 0):.3f}</dd></div>'
                f'<div><dt>Edge</dt><dd>{escape(percent_ratio(market.get("profit_margin")))}</dd></div>'
                '</dl>'
                '<div class="market-bottomline">'
                f'<span>Volume {float(market.get("volume", 0) or 0):,.2f}</span>'
                f'<span>{"Tradeable edge" if market.get("opportunity") else "Watch window"}</span>'
                "</div></article>"
            )
        )
    return "".join(cards)


def render_position_cards_html(positions: Iterable[dict], settled: bool = False) -> str:
    position_list = list(positions)
    if not position_list:
        return (
            '<p class="empty">No settled pairs yet.</p>'
            if settled
            else '<p class="empty">No open settlement queue.</p>'
        )

    current = now_utc()
    target_key = "settled_at" if settled else "resolve_at"
    position_list.sort(
        key=lambda position: parse_ts(position.get(target_key)) or current,
        reverse=settled,
    )

    cards = []
    for position in position_list:
        target = parse_ts(position.get(target_key))
        tone = (
            "good"
            if not settled or float(position.get("profit", 0) or 0) >= 0
            else "danger"
        )
        amount_key = "profit" if settled else "expected_profit"
        cards.append(
            (
                f'<article class="mini-card tone-{escape(tone)}">'
                f'<div class="mini-topline"><strong>{escape(str(position.get("asset", "")))}</strong>'
                f'{chip(str(position.get("data_source", "queue" if not settled else "settled")), "cool" if settled else source_tone(str(position.get("data_source"))))}</div>'
                f'<h3>{escape(str(position.get("slug") or position.get("question") or "Paper pair"))}</h3>'
                f'<p class="mini-copy">{escape(str(position.get("question", "Paper arbitrage position")))}</p>'
                '<dl class="mini-grid">'
                f'<div><dt>Stake</dt><dd>{escape(money(position.get("cost")))}</dd></div>'
                f'<div><dt>{"Profit" if settled else "Expected"}</dt><dd>{escape(money(position.get(amount_key)))}</dd></div>'
                '</dl>'
                f'<div class="mini-meta">{"Settled" if settled else "Settle"} {escape(human_delta(target, current))} · {escape(format_abs(target))}</div>'
                "</article>"
            )
        )
    return "".join(cards)


def render_test_runs_html(items: Iterable[dict]) -> str:
    test_list = list(items)
    if not test_list:
        return '<p class="empty">No test runs yet.</p>'

    cards = []
    for item in test_list[:10]:
        tone = test_tone(item.get("status"))
        cards.append(
            (
                f'<article class="stack-card tone-{escape(tone)}">'
                f'<div class="stack-topline"><strong>{escape(str(item.get("status", "unknown")).upper())}</strong>'
                f'{chip(str(item.get("status", "unknown")), tone)}</div>'
                f'<p class="stack-copy">{escape(f"{item.get("passed", 0)}/{item.get("collected", 0)} passed · {format_abs(item.get("timestamp"))}")}</p>'
                f'<pre>{escape(str((item.get("stderr") or item.get("stdout") or "No console output")[:700]))}</pre>'
                "</article>"
            )
        )
    return "".join(cards)


def render_logs_html(items: Iterable[dict]) -> str:
    log_list = list(items)
    if not log_list:
        return '<p class="empty">No journal entries yet.</p>'

    cards = []
    for item in log_list[:20]:
        tone = log_tone(item.get("level"))
        cards.append(
            (
                f'<article class="stack-card tone-{escape(tone)}">'
                f'<div class="stack-topline"><strong>{escape(str(item.get("level", "LOG")))}</strong>'
                f'{chip(str(item.get("level", "LOG")), tone)}</div>'
                f'<p class="stack-copy">{escape(str(item.get("message", "")))}</p>'
                f'<pre>{escape(json.dumps(item.get("payload") or {}, indent=2))}</pre>'
                "</article>"
            )
        )
    return "".join(cards)


def render_index_html(
    snapshot: dict, status_message: Optional[str] = None, status_tone: str = "ok"
) -> str:
    current = now_utc()
    template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    summary = snapshot.get("summary", {})
    service_state = snapshot.get("service", {})
    next_scan_at = next_scan(snapshot, current)
    refresh_seconds = max(int(service_state.get("scan_interval_seconds") or 15) + 2, 15)

    if service_state.get("tests_running"):
        badge_text = "Tests Running"
        badge_class = "warn"
    elif summary.get("last_error"):
        badge_text = "Degraded"
        badge_class = "danger"
    else:
        badge_text = "Live"
        badge_class = "ok"

    if not status_message:
        status_message = f"Live time screen ready. Full refresh every {refresh_seconds}s."

    initial_json = json.dumps(snapshot).replace("</", "<\\/")
    replacements = {
        "__SERVER_STATUS_TEXT__": escape(status_message),
        "__SERVER_STATUS_CLASS__": escape(status_tone or "ok"),
        "__SERVER_HERO_CLOCK_PRIMARY__": current.strftime("%H:%M:%S"),
        "__SERVER_HERO_CLOCK_META__": f"Server UTC · next scan {human_delta(next_scan_at, current)}",
        "__SERVER_TEMPO_CARDS__": render_tempo_cards_html(snapshot),
        "__SERVER_BADGE_TEXT__": badge_text,
        "__SERVER_BADGE_CLASS__": badge_class,
        "__SERVER_TIME_RAIL__": render_time_rail_html(snapshot),
        "__SERVER_RUNTIME__": render_runtime_html(snapshot),
        "__SERVER_MARKETS__": render_market_cards_html(snapshot.get("active_markets", [])),
        "__SERVER_OPEN_POSITIONS__": render_position_cards_html(snapshot.get("open_positions", [])),
        "__SERVER_CLOSED_POSITIONS__": render_position_cards_html(
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
    title="Money House Time Admin",
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


def action_redirect(message: str, tone: str = "ok") -> RedirectResponse:
    query = urlencode({"message": message, "tone": tone})
    return RedirectResponse(url=f"/?{query}", status_code=303)


@app.post("/actions/scan")
async def scan_once_page() -> RedirectResponse:
    try:
        await service.scan_once()
        return action_redirect("Manual scan completed.", "ok")
    except Exception as exc:  # pragma: no cover - defensive UI route
        logger.exception("Manual scan failed")
        return action_redirect(f"Manual scan failed: {exc}", "danger")


@app.post("/actions/reset")
async def reset_bankroll_page() -> RedirectResponse:
    try:
        await service.reset_bankroll()
        return action_redirect("Bankroll reset completed.", "warn")
    except Exception as exc:  # pragma: no cover - defensive UI route
        logger.exception("Bankroll reset failed")
        return action_redirect(f"Reset failed: {exc}", "danger")


@app.post("/actions/tests")
async def run_tests_page() -> RedirectResponse:
    try:
        await service.trigger_tests()
        return action_redirect("Test run started.", "warn")
    except RuntimeError as exc:
        return action_redirect(f"Could not start test run: {exc}", "danger")
    except Exception as exc:  # pragma: no cover - defensive UI route
        logger.exception("Test run failed")
        return action_redirect(f"Could not start test run: {exc}", "danger")


@app.api_route("/", methods=["GET", "HEAD"])
async def index(request: Request) -> HTMLResponse:
    status_message = request.query_params.get("message")
    status_tone = request.query_params.get("tone", "ok")
    return HTMLResponse(render_index_html(service.snapshot(), status_message, status_tone))


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg")
