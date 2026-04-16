#!/usr/bin/env python3
"""
Paper trading service and state persistence for the Money House admin dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from .gamma_client import GammaClient


PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "paper_dashboard_state.json"
TEST_REPORT_PATH = DATA_DIR / "test-report.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class MarketSnapshot:
    asset: str
    slug: str
    question: str
    yes_price: float
    no_price: float
    combined_cost: float
    profit_margin: float
    end_date: str
    volume: float
    accepting_orders: bool
    data_source: str
    opportunity: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "slug": self.slug,
            "question": self.question,
            "yes_price": round(self.yes_price, 4),
            "no_price": round(self.no_price, 4),
            "combined_cost": round(self.combined_cost, 4),
            "profit_margin": round(self.profit_margin, 4),
            "end_date": self.end_date,
            "volume": round(self.volume, 2),
            "accepting_orders": self.accepting_orders,
            "data_source": self.data_source,
            "opportunity": self.opportunity,
        }


class PaperTradingService:
    """
    Background scanner that simulates arbitrage pairs with fake money only.
    """

    def __init__(
        self,
        state_path: Optional[Path] = None,
        test_report_path: Optional[Path] = None,
    ) -> None:
        self.logger = logging.getLogger("paper_service")
        self._lock = RLock()
        self._task: Optional[asyncio.Task] = None
        self._tests_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self.gamma = GammaClient()
        self.state_path = state_path or STATE_PATH
        self.test_report_path = test_report_path or TEST_REPORT_PATH

        self.scan_interval = int(os.environ.get("MONEY_SCAN_INTERVAL", "15"))
        self.starting_balance = float(os.environ.get("MONEY_STARTING_BALANCE", "1000"))
        self.max_trade_stake = float(os.environ.get("MONEY_MAX_TRADE_STAKE", "120"))
        raw_assets = os.environ.get("MONEY_TARGET_ASSETS", "BTC,ETH,SOL")
        self.assets = [item.strip().upper() for item in raw_assets.split(",") if item.strip()]

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _default_state(self) -> Dict[str, Any]:
        return {
            "mode": "paper-only",
            "banner": "Fake money only. No wallet keys. No live order execution.",
            "created_at": iso_now(),
            "cash_balance": round(self.starting_balance, 2),
            "starting_balance": round(self.starting_balance, 2),
            "scan_interval_seconds": self.scan_interval,
            "scan_count": 0,
            "last_scan_at": None,
            "last_error": None,
            "last_data_source": "bootstrap",
            "is_scanning": False,
            "open_positions": [],
            "closed_positions": [],
            "active_markets": [],
            "logs": [],
            "test_runs": [],
        }

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            state = self._default_state()
            self._persist(state)
            return state

        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            merged = self._default_state()
            merged.update(data)
            return merged
        except Exception as exc:
            self.logger.warning("Falling back to empty paper state: %s", exc)
            return self._default_state()

    def _persist(self, state: Optional[Dict[str, Any]] = None) -> None:
        payload = state if state is not None else self.state
        temp_path = self.state_path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        temp_path.replace(self.state_path)

    def _append_log(
        self,
        level: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "timestamp": iso_now(),
            "level": level.upper(),
            "message": message,
            "payload": payload or {},
        }
        self.state["logs"].append(entry)
        self.state["logs"] = self.state["logs"][-60:]
        self._persist()

    def _summary(self) -> Dict[str, Any]:
        open_positions = self.state["open_positions"]
        closed_positions = self.state["closed_positions"]
        open_exposure = sum(item["cost"] for item in open_positions)
        expected_value = sum(item["payout"] for item in open_positions)
        open_profit = sum(item["expected_profit"] for item in open_positions)
        realized_profit = sum(item["profit"] for item in closed_positions)
        settled_count = len(closed_positions)
        winning_count = len([item for item in closed_positions if item["profit"] > 0])
        success_rate = winning_count / settled_count if settled_count else 0.0
        equity = self.state["cash_balance"] + expected_value

        return {
            "starting_balance": round(self.state["starting_balance"], 2),
            "cash_balance": round(self.state["cash_balance"], 2),
            "equity": round(equity, 2),
            "open_exposure": round(open_exposure, 2),
            "open_profit": round(open_profit, 2),
            "realized_profit": round(realized_profit, 2),
            "open_positions": len(open_positions),
            "closed_positions": settled_count,
            "success_rate": round(success_rate, 4),
            "scan_count": self.state["scan_count"],
            "last_scan_at": self.state["last_scan_at"],
            "last_data_source": self.state["last_data_source"],
            "last_error": self.state["last_error"],
            "mode": self.state["mode"],
            "banner": self.state["banner"],
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "summary": self._summary(),
                "service": {
                    "scan_interval_seconds": self.scan_interval,
                    "is_scanning": self.state["is_scanning"],
                    "tests_running": self._tests_task is not None and not self._tests_task.done(),
                    "assets": self.assets,
                },
                "active_markets": list(self.state["active_markets"]),
                "open_positions": list(self.state["open_positions"]),
                "closed_positions": list(reversed(self.state["closed_positions"][-20:])),
                "logs": list(reversed(self.state["logs"][-20:])),
                "test_runs": list(reversed(self.state["test_runs"][-10:])),
            }

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._shutdown.clear()
        self._task = asyncio.create_task(self._run_loop(), name="paper-trading-loop")
        self._append_log("info", "Paper trading loop started", {"assets": self.assets})

    async def stop(self) -> None:
        self._shutdown.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._append_log("info", "Paper trading loop stopped")

    async def _run_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self.scan_once()
            except Exception as exc:
                with self._lock:
                    self.state["last_error"] = str(exc)
                    self._persist()
                self._append_log("error", "Background scan failed", {"error": str(exc)})
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self.scan_interval)
            except asyncio.TimeoutError:
                continue

    async def scan_once(self) -> Dict[str, Any]:
        with self._lock:
            self.state["is_scanning"] = True
            self._persist()

        try:
            snapshots = await self._collect_market_snapshots()
            with self._lock:
                self.state["active_markets"] = [item.as_dict() for item in snapshots]
                self.state["scan_count"] += 1
                self.state["last_scan_at"] = iso_now()
                if snapshots:
                    self.state["last_data_source"] = snapshots[0].data_source

                self._settle_due_positions_locked()
                self._open_new_positions_locked(snapshots)
                self._persist()

            return self.snapshot()
        finally:
            with self._lock:
                self.state["is_scanning"] = False
                self._persist()

    async def _collect_market_snapshots(self) -> List[MarketSnapshot]:
        live_snapshots = await asyncio.to_thread(self._fetch_gamma_snapshots)
        if live_snapshots:
            if not any(item.opportunity for item in live_snapshots):
                demo_snapshots = self._generate_demo_snapshots()
                return live_snapshots + demo_snapshots
            return live_snapshots
        return self._generate_demo_snapshots()

    def _fetch_gamma_snapshots(self) -> List[MarketSnapshot]:
        snapshots: List[MarketSnapshot] = []
        for asset in self.assets:
            info = self.gamma.get_market_info(asset)
            if not info:
                continue

            prices = info.get("prices", {})
            yes_price = float(prices.get("up") or prices.get("yes") or 0)
            no_price = float(prices.get("down") or prices.get("no") or 0)
            if yes_price <= 0 or no_price <= 0:
                continue

            combined_cost = yes_price + no_price
            profit_margin = max(0.0, 1.0 - combined_cost)
            end_date = info.get("end_date") or (
                utc_now() + timedelta(minutes=15)
            ).isoformat()
            snapshots.append(
                MarketSnapshot(
                    asset=asset,
                    slug=info.get("slug") or f"{asset.lower()}-unknown",
                    question=info.get("question") or f"{asset} 15 minute market",
                    yes_price=yes_price,
                    no_price=no_price,
                    combined_cost=combined_cost,
                    profit_margin=profit_margin,
                    end_date=end_date,
                    volume=float(info.get("volume") or 0),
                    accepting_orders=bool(info.get("accepting_orders")),
                    data_source="gamma",
                    opportunity=combined_cost < 0.97 and profit_margin >= 0.02,
                )
            )
        return snapshots

    def _generate_demo_snapshots(self) -> List[MarketSnapshot]:
        now = utc_now()
        snapshots: List[MarketSnapshot] = []
        for index, asset in enumerate(self.assets):
            phase = now.minute / 60 + index * 0.73
            center = 0.47 + (math.sin(phase * math.pi * 2) * 0.025)
            edge = 0.47 + (math.cos(phase * math.pi * 2) * 0.02)
            yes_price = max(0.36, min(0.61, round(center, 4)))
            no_price = max(0.36, min(0.61, round(edge, 4)))
            combined_cost = round(yes_price + no_price, 4)
            profit_margin = round(max(0.0, 1.0 - combined_cost), 4)
            end_window = now.replace(second=0, microsecond=0)
            end_window += timedelta(minutes=15 - (end_window.minute % 15 or 15))
            snapshots.append(
                MarketSnapshot(
                    asset=asset,
                    slug=f"demo-{asset.lower()}-{end_window.strftime('%Y%m%d%H%M')}",
                    question=f"Demo {asset}: higher in the next 15 minutes?",
                    yes_price=yes_price,
                    no_price=no_price,
                    combined_cost=combined_cost,
                    profit_margin=profit_margin,
                    end_date=end_window.isoformat(),
                    volume=round(25000 + abs(math.sin(phase)) * 14000, 2),
                    accepting_orders=True,
                    data_source="demo",
                    opportunity=combined_cost < 0.97 and profit_margin >= 0.02,
                )
            )

        return snapshots

    def _settle_due_positions_locked(self) -> None:
        now = utc_now()
        still_open: List[Dict[str, Any]] = []
        for position in self.state["open_positions"]:
            resolve_at = parse_iso(position["resolve_at"])
            if now < resolve_at + timedelta(seconds=45):
                still_open.append(position)
                continue

            self.state["cash_balance"] += position["payout"]
            position["status"] = "settled"
            position["settled_at"] = iso_now()
            self.state["closed_positions"].append(position)
            self._append_log(
                "info",
                "Settled paper trade",
                {
                    "asset": position["asset"],
                    "slug": position["slug"],
                    "profit": round(position["profit"], 2),
                },
            )

        self.state["open_positions"] = still_open
        self.state["closed_positions"] = self.state["closed_positions"][-60:]

    def _open_new_positions_locked(self, snapshots: List[MarketSnapshot]) -> None:
        open_slugs = {item["slug"] for item in self.state["open_positions"]}
        for snapshot in snapshots:
            if not snapshot.opportunity or snapshot.slug in open_slugs:
                continue

            if len(self.state["open_positions"]) >= 4:
                return

            stake = min(self.max_trade_stake, self.state["cash_balance"] * 0.32)
            if stake < 25:
                self._append_log("warning", "Skipped paper trade because fake cash is too low")
                return

            shares = round(stake / snapshot.combined_cost, 4)
            cost = round(shares * snapshot.combined_cost, 4)
            payout = round(shares, 4)
            profit = round(payout - cost, 4)
            if cost > self.state["cash_balance"]:
                continue

            self.state["cash_balance"] = round(self.state["cash_balance"] - cost, 4)
            self.state["open_positions"].append(
                {
                    "asset": snapshot.asset,
                    "slug": snapshot.slug,
                    "question": snapshot.question,
                    "yes_price": snapshot.yes_price,
                    "no_price": snapshot.no_price,
                    "combined_cost": snapshot.combined_cost,
                    "profit_margin": snapshot.profit_margin,
                    "shares": shares,
                    "cost": cost,
                    "payout": payout,
                    "expected_profit": profit,
                    "profit": profit,
                    "opened_at": iso_now(),
                    "resolve_at": snapshot.end_date,
                    "status": "open",
                    "data_source": snapshot.data_source,
                }
            )
            self._append_log(
                "info",
                "Opened paper arbitrage pair",
                {
                    "asset": snapshot.asset,
                    "slug": snapshot.slug,
                    "cost": round(cost, 2),
                    "profit": round(profit, 2),
                },
            )

    async def reset_bankroll(self) -> Dict[str, Any]:
        with self._lock:
            preserved_tests = list(self.state["test_runs"])
            self.state = self._default_state()
            self.state["test_runs"] = preserved_tests
            self._persist()
        self._append_log("warning", "Paper bankroll reset")
        return self.snapshot()

    async def trigger_tests(self) -> Dict[str, Any]:
        if self._tests_task and not self._tests_task.done():
            raise RuntimeError("Tests are already running")
        self._tests_task = asyncio.create_task(self._run_tests(), name="dashboard-tests")
        return {"status": "started"}

    async def _run_tests(self) -> None:
        self._append_log("info", "Started unit test run")
        report_file = self.test_report_path
        report_file.parent.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit",
            "-q",
            "--json-report",
            f"--json-report-file={report_file}",
        ]

        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

        summary: Dict[str, Any] = {
            "timestamp": iso_now(),
            "status": "passed" if completed.returncode == 0 else "failed",
            "return_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "report_path": str(report_file),
        }

        if report_file.exists():
            try:
                with report_file.open("r", encoding="utf-8") as handle:
                    report = json.load(handle)
                report_summary = report.get("summary", {})
                summary.update(
                    {
                        "collected": report_summary.get("collected"),
                        "passed": report_summary.get("passed"),
                        "failed": report_summary.get("failed"),
                        "errors": report_summary.get("error"),
                        "duration_seconds": report.get("duration"),
                    }
                )
            except Exception as exc:
                summary["report_error"] = str(exc)

        with self._lock:
            self.state["test_runs"].append(summary)
            self.state["test_runs"] = self.state["test_runs"][-12:]
            self._persist()

        self._append_log("info", "Finished unit test run", {"status": summary["status"]})
