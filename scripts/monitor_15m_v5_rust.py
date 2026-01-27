#!/usr/bin/env python3
"""
Gabagool 15-Minute Market Monitor v5-Rust - Rust Hot Path Integration

Purpose:
    High-frequency order book capture with Rust-accelerated hot path.
    Uses Rust components for arbitrage detection, position tracking, and execution.
    Python handles market scanning (API clients) and data management.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Strategy:
    Same as v5 (liquidity-first stacking) but with Rust hot path:
    - Rust: Strategy detection, position tracking, order execution
    - Python: Market scanning, data persistence, logging

Performance Target:
    - Hot path latency: <40ms (vs 380ms pure Python)
    - Speedup: 9-10x on critical path

Dependencies:
    - gabagool_rust (Rust extension via PyO3)
    - py_clob_client
    - src.gamma_client

Usage:
    python scripts/monitor_15m_v5_rust.py --instance rust_test --fresh

Output:
    - data/logs/market_snapshots_YYYYMMDD.jsonl
    - data/logs/opportunities_YYYYMMDD.jsonl
    - data/logs/{instance}_paper_trades.jsonl
    - data/logs/{instance}_paper_pnl_summary.json

Notes:
    - This is an ADDITIVE script - does not replace monitor_15m_v5.py
    - Requires gabagool_rust module (maturin develop)
    - Run alongside existing monitors for A/B comparison
"""

import sys
import os
import time
import json
import threading
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from enum import Enum

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Python API clients (kept for market scanning)
from src.gamma_client import GammaClient
from py_clob_client.client import ClobClient

# Rust hot path components
try:
    import gabagool_rust
    RUST_AVAILABLE = True
    print(f"Rust hot path: {gabagool_rust.health_check()}")
except ImportError as e:
    RUST_AVAILABLE = False
    print(f"WARNING: Rust module not available ({e}). Run 'maturin develop' in rust/")
    sys.exit(1)


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Monitor configuration."""
    coins: List[str] = None
    scan_interval_ms: int = 500
    book_depth: int = 10

    # Opportunity detection
    opportunity_threshold: float = 1.00

    # Paper trading
    initial_equity: float = 2000.0
    max_trade_size: float = 100.0
    max_position_pct: float = 0.25
    gas_cost_per_tx: float = 0.003
    min_margin_to_trade: float = 0.005

    # Stacking
    max_stack_per_window: int = 10
    min_seconds_between_stack: float = 0.0

    # Risk Management
    daily_loss_limit_pct: float = 0.20
    max_open_positions: int = 20

    # Data retention
    retention_hours: int = 8
    cleanup_interval_mins: int = 15

    # Output paths
    log_dir: str = "data/logs"
    console_log: str = "logs/monitor_v5_rust.log"
    max_workers: int = 8

    # Instance isolation
    instance: str = "rust_v5"
    fresh_start: bool = False

    def __post_init__(self):
        if self.coins is None:
            self.coins = ["BTC", "ETH", "SOL", "XRP"]


# =============================================================================
# DATA STRUCTURES (Python side - for compatibility)
# =============================================================================

@dataclass
class OrderLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    bids: List[OrderLevel]
    asks: List[OrderLevel]
    best_bid: float
    best_ask: float
    spread: float


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


@dataclass
class PaperTrade:
    """Paper trade record for persistence."""
    trade_id: str
    coin: str
    slug: str
    window_end_ts: int
    entry_ts: str
    entry_ts_epoch_ms: int
    entry_combined_ask: float
    entry_up_ask: float
    entry_down_ask: float
    entry_up_size: float
    entry_down_size: float
    trade_size_usd: float
    tokens_acquired: float
    entry_cost: float
    gas_cost: float
    exit_ts: Optional[str] = None
    exit_ts_epoch_ms: Optional[int] = None
    payout: Optional[float] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None
    status: TradeStatus = TradeStatus.OPEN
    execution_time_ms: Optional[int] = None  # Rust execution time


@dataclass
class PnLSummary:
    """Cumulative P&L tracking."""
    initial_equity: float = 2000.0
    current_equity: float = 2000.0
    equity_in_positions: float = 0.0
    daily_start_equity: float = 2000.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_date: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_gross_pnl: float = 0.0
    total_gas_paid: float = 0.0
    total_net_pnl: float = 0.0
    total_volume: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    avg_margin_at_entry: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 2000.0
    trading_halted: bool = False
    halt_reason: str = ""
    last_updated: str = ""
    # Rust performance stats
    avg_hot_path_latency_ms: float = 0.0
    total_rust_executions: int = 0


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(console_log_path: str) -> logging.Logger:
    """Setup dual logging to console and file."""
    logger = logging.getLogger("monitor_v5_rust")
    logger.setLevel(logging.INFO)
    logger.handlers = []

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%H:%M:%S'))
    logger.addHandler(console)

    Path(console_log_path).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(console_log_path, mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger.addHandler(file_handler)

    return logger


# =============================================================================
# DATA CLEANUP
# =============================================================================

class DataCleaner:
    """Manages automatic cleanup of old snapshot data."""

    def __init__(self, log_dir: str, retention_hours: int, cleanup_interval_mins: int, logger: logging.Logger):
        self.log_dir = Path(log_dir)
        self.retention_hours = retention_hours
        self.cleanup_interval_mins = cleanup_interval_mins
        self.logger = logger
        self._stop_event = threading.Event()
        self._thread = None

    def _get_old_snapshot_files(self) -> List[Path]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        old_files = []
        for filepath in self.log_dir.glob("market_snapshots_*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    old_files.append(filepath)
            except Exception:
                continue
        return old_files

    def _cleanup_old_files(self) -> int:
        old_files = self._get_old_snapshot_files()
        deleted = 0
        for filepath in old_files:
            try:
                size_mb = filepath.stat().st_size / (1024 * 1024)
                filepath.unlink()
                self.logger.info(f"Cleaned up: {filepath.name} ({size_mb:.1f} MB)")
                deleted += 1
            except Exception as e:
                self.logger.warning(f"Failed to delete {filepath}: {e}")
        return deleted

    def _cleanup_loop(self):
        while not self._stop_event.wait(self.cleanup_interval_mins * 60):
            try:
                deleted_files = self._cleanup_old_files()
                if deleted_files > 0:
                    self.logger.info(f"Cleanup: {deleted_files} files removed")
            except Exception as e:
                self.logger.warning(f"Cleanup error: {e}")

    def start(self):
        self.logger.info(f"Data retention: {self.retention_hours}h (cleanup every {self.cleanup_interval_mins}m)")
        deleted = self._cleanup_old_files()
        if deleted > 0:
            self.logger.info(f"Initial cleanup: removed {deleted} old files")
        self._thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)


# =============================================================================
# RUST-POWERED PAPER TRADER
# =============================================================================

class RustPaperTrader:
    """
    Paper trading system using Rust hot path components.

    Uses:
    - gabagool_rust.GabagoolStrategy for opportunity detection
    - gabagool_rust.OrderExecutor for paper execution
    - gabagool_rust.PositionTracker for position management
    - gabagool_rust.PriceFeedCache for price caching

    Python handles:
    - State persistence (JSONL files)
    - Risk management (daily limits, max positions)
    - Logging and output
    """

    def __init__(self, config: Config, log_dir: str, logger: logging.Logger):
        self.config = config
        self.log_dir = Path(log_dir)
        self.logger = logger
        self.instance = config.instance

        # Rust components
        self.strategy = gabagool_rust.GabagoolStrategy(min_margin=config.min_margin_to_trade)
        self.executor = gabagool_rust.OrderExecutor(paper_mode=True, gas_per_tx=config.gas_cost_per_tx)
        self.tracker = gabagool_rust.PositionTracker()
        self.price_cache = gabagool_rust.PriceFeedCache()

        # Python state for persistence
        self.open_positions: Dict[str, PaperTrade] = {}
        self.stack_counts: Dict[str, Dict[int, int]] = {}
        self.last_trade_time: Dict[str, float] = {}
        self.trade_counter = 0

        # P&L summary (Python side for persistence)
        self.pnl_summary = PnLSummary(
            initial_equity=config.initial_equity,
            current_equity=config.initial_equity,
            daily_start_equity=config.initial_equity,
            peak_equity=config.initial_equity
        )

        # Files
        self.trades_file = self.log_dir / f"{self.instance}_paper_trades.jsonl"
        self.pnl_file = self.log_dir / f"{self.instance}_paper_pnl_summary.json"

        # Performance tracking
        self.hot_path_latencies: List[float] = []

        # Load existing state
        if config.fresh_start:
            self.logger.info(f"FRESH START: Instance '{self.instance}' starting with ${config.initial_equity:.0f}")
        else:
            self._load_state()

    def _load_state(self):
        """Load existing P&L summary and open positions."""
        if self.pnl_file.exists():
            try:
                with open(self.pnl_file, 'r') as f:
                    data = json.load(f)
                    self.pnl_summary = PnLSummary(**{k: v for k, v in data.items() if k in PnLSummary.__dataclass_fields__})
                    self.trade_counter = self.pnl_summary.total_trades
                    self.logger.info(
                        f"Loaded P&L: {self.pnl_summary.total_trades} trades, "
                        f"Equity=${self.pnl_summary.current_equity:.2f}"
                    )
            except Exception as e:
                self.logger.warning(f"Failed to load P&L summary: {e}")

        if self.trades_file.exists():
            try:
                with open(self.trades_file, 'r') as f:
                    for line in f:
                        trade = json.loads(line)
                        if trade.get('status') == 'OPEN':
                            trade_obj = self._dict_to_trade(trade)
                            self.open_positions[trade_obj.trade_id] = trade_obj
                            # Also add to Rust tracker
                            self.tracker.add_yes_position(
                                trade_obj.slug,
                                trade_obj.tokens_acquired,
                                trade_obj.tokens_acquired * trade_obj.entry_up_ask
                            )
                            self.tracker.add_no_position(
                                trade_obj.slug,
                                trade_obj.tokens_acquired,
                                trade_obj.tokens_acquired * trade_obj.entry_down_ask
                            )
                if self.open_positions:
                    self.logger.info(f"Restored {len(self.open_positions)} open positions")
                    self.pnl_summary.equity_in_positions = sum(
                        t.entry_cost for t in self.open_positions.values()
                    )
            except Exception as e:
                self.logger.warning(f"Failed to load open positions: {e}")

        self._check_daily_reset()

    def _dict_to_trade(self, d: Dict) -> PaperTrade:
        """Convert dict to PaperTrade."""
        d['status'] = TradeStatus(d.get('status', 'OPEN'))
        return PaperTrade(**{k: v for k, v in d.items() if k in PaperTrade.__dataclass_fields__})

    def _trade_to_dict(self, trade: PaperTrade) -> Dict:
        """Convert PaperTrade to dict for JSON."""
        d = asdict(trade)
        d['status'] = trade.status.value
        return d

    def _save_trade(self, trade: PaperTrade):
        """Append trade to permanent file."""
        with open(self.trades_file, 'a') as f:
            f.write(json.dumps(self._trade_to_dict(trade), separators=(',', ':')) + '\n')

    def _save_pnl_summary(self):
        """Save P&L summary."""
        self.pnl_summary.last_updated = datetime.now(timezone.utc).isoformat()
        with open(self.pnl_file, 'w') as f:
            json.dump(asdict(self.pnl_summary), f, indent=2)

    def _generate_trade_id(self) -> str:
        """Generate unique trade ID."""
        self.trade_counter += 1
        return f"PT-RUST-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{self.trade_counter:05d}"

    def _get_window_end_timestamp(self, slug: str) -> int:
        """Extract window end timestamp from slug."""
        try:
            parts = slug.split('-')
            return int(parts[-1]) + 900
        except:
            return 0

    def _check_daily_reset(self):
        """Check if we need to reset daily stats."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.pnl_summary.daily_date != today:
            if self.pnl_summary.daily_date:
                self.logger.info(f"New day: resetting daily stats (previous: ${self.pnl_summary.daily_pnl:+.2f})")
            self.pnl_summary.daily_date = today
            self.pnl_summary.daily_start_equity = self.pnl_summary.current_equity
            self.pnl_summary.daily_pnl = 0.0
            self.pnl_summary.daily_trades = 0
            self.pnl_summary.trading_halted = False
            self.pnl_summary.halt_reason = ""
            self._save_pnl_summary()

    def _can_stack_trade(self, coin: str, window_ts: int) -> tuple:
        """Check if we can stack another trade."""
        if coin not in self.stack_counts:
            self.stack_counts[coin] = {}
        current_count = self.stack_counts[coin].get(window_ts, 0)
        if current_count >= self.config.max_stack_per_window:
            return False, f"max_stack_reached ({current_count}/{self.config.max_stack_per_window})"
        last_trade = self.last_trade_time.get(coin, 0)
        elapsed = time.time() - last_trade
        if elapsed < self.config.min_seconds_between_stack:
            return False, f"cooldown ({elapsed:.1f}s)"
        return True, "ok"

    def _record_stack_trade(self, coin: str, window_ts: int):
        """Record that we stacked a trade."""
        if coin not in self.stack_counts:
            self.stack_counts[coin] = {}
        self.stack_counts[coin][window_ts] = self.stack_counts[coin].get(window_ts, 0) + 1
        self.last_trade_time[coin] = time.time()

    def _check_risk_limits(self) -> tuple:
        """Check if we can trade based on risk limits."""
        if self.pnl_summary.trading_halted:
            return False, f"trading_halted: {self.pnl_summary.halt_reason}"
        daily_loss_limit = self.pnl_summary.daily_start_equity * self.config.daily_loss_limit_pct
        if self.pnl_summary.daily_pnl < -daily_loss_limit:
            self.pnl_summary.trading_halted = True
            self.pnl_summary.halt_reason = f"daily_loss_limit (${self.pnl_summary.daily_pnl:.2f})"
            self._save_pnl_summary()
            self.logger.warning(f"TRADING HALTED: Daily loss limit hit")
            return False, self.pnl_summary.halt_reason
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, f"max_positions ({len(self.open_positions)}/{self.config.max_open_positions})"
        available = self.pnl_summary.current_equity - self.pnl_summary.equity_in_positions
        if available < 1.0:
            return False, f"insufficient_equity (${available:.2f})"
        return True, "ok"

    def _calculate_liquidity_position_size(self, up_best_size: float, up_best_price: float,
                                           down_best_size: float, down_best_price: float) -> float:
        """Calculate position size based on liquidity at best price."""
        available_equity = self.pnl_summary.current_equity - self.pnl_summary.equity_in_positions
        if available_equity <= 0:
            return 0
        up_best_value = up_best_size * up_best_price
        down_best_value = down_best_size * down_best_price
        if up_best_value <= 0 or down_best_value <= 0:
            return 0
        min_side_liquidity = min(up_best_value, down_best_value)
        liquidity_limited_size = min_side_liquidity * 2
        max_size = min(
            self.config.max_trade_size,
            available_equity,
            liquidity_limited_size
        )
        return max_size if max_size > 0 else 0

    def check_for_opportunity_rust(self, coin: str, slug: str, up_ask: float, down_ask: float,
                                   up_size: float, down_size: float, remaining_seconds: float,
                                   full_record: Dict) -> Optional[PaperTrade]:
        """
        Check for opportunity using RUST hot path.

        This is the main integration point where we use Rust components.
        """
        self._check_daily_reset()

        # Update Rust price cache
        self.price_cache.update_snapshot(slug, coin, up_ask, up_size, down_ask, down_size)

        combined_ask = up_ask + down_ask

        # Check threshold
        if combined_ask >= self.config.opportunity_threshold:
            return None

        # Check time remaining
        if remaining_seconds < 30:
            return None

        # Use RUST strategy to detect arbitrage
        hot_path_start = time.perf_counter()
        opp = self.strategy.detect_arbitrage(slug, coin, up_ask, down_ask, up_size, down_size)
        hot_path_elapsed = (time.perf_counter() - hot_path_start) * 1000

        if not opp:
            return None

        # Check Python-side risk limits
        can_trade, risk_reason = self._check_risk_limits()
        if not can_trade:
            return None

        # Check stacking
        window_ts = self._get_window_end_timestamp(slug) - 900
        can_stack, stack_reason = self._can_stack_trade(coin, window_ts)
        if not can_stack:
            return None

        # Calculate position size
        trade_size = self._calculate_liquidity_position_size(up_size, up_ask, down_size, down_ask)
        if trade_size < 1.0:
            return None

        # Use RUST executor for paper trade
        exec_start = time.perf_counter()
        exec_result = self.executor.execute_arbitrage(slug, up_ask, down_ask, trade_size / combined_ask)
        exec_elapsed = (time.perf_counter() - exec_start) * 1000

        total_hot_path_ms = hot_path_elapsed + exec_elapsed

        if not exec_result.get('success'):
            return None

        # Track performance
        self.hot_path_latencies.append(total_hot_path_ms)
        self.pnl_summary.total_rust_executions += 1
        if self.hot_path_latencies:
            self.pnl_summary.avg_hot_path_latency_ms = sum(self.hot_path_latencies) / len(self.hot_path_latencies)

        # Create trade record
        entry_cost = trade_size
        tokens_acquired = trade_size / combined_ask
        gas_cost = self.config.gas_cost_per_tx * 2
        now = datetime.now(timezone.utc)

        trade = PaperTrade(
            trade_id=self._generate_trade_id(),
            coin=coin,
            slug=slug,
            window_end_ts=self._get_window_end_timestamp(slug),
            entry_ts=now.isoformat(),
            entry_ts_epoch_ms=int(now.timestamp() * 1000),
            entry_combined_ask=combined_ask,
            entry_up_ask=up_ask,
            entry_down_ask=down_ask,
            entry_up_size=up_size,
            entry_down_size=down_size,
            trade_size_usd=trade_size,
            tokens_acquired=tokens_acquired,
            entry_cost=entry_cost,
            gas_cost=gas_cost,
            status=TradeStatus.OPEN,
            execution_time_ms=int(total_hot_path_ms)
        )

        # Update Rust position tracker
        self.tracker.add_yes_position(slug, tokens_acquired, tokens_acquired * up_ask)
        self.tracker.add_no_position(slug, tokens_acquired, tokens_acquired * down_ask)

        # Update Python state
        self.pnl_summary.equity_in_positions += entry_cost
        self.open_positions[trade.trade_id] = trade
        self._record_stack_trade(coin, window_ts)
        self._save_trade(trade)
        self._save_pnl_summary()

        stack_count = self.stack_counts.get(coin, {}).get(window_ts, 0)
        margin = 1.0 - combined_ask
        self.logger.info(
            f"RUST TRADE: {trade.trade_id} | {coin} | "
            f"Combined={combined_ask:.4f} | Margin={margin:.2%} | "
            f"Size=${trade_size:.2f} | HotPath={total_hot_path_ms:.1f}ms | "
            f"Stack={stack_count}/{self.config.max_stack_per_window}"
        )

        return trade

    def check_for_closures(self, current_window_ts: int) -> List[PaperTrade]:
        """Check if any open positions should be closed."""
        closed = []
        now = datetime.now(timezone.utc)
        current_ts = int(now.timestamp())

        for trade_id, trade in list(self.open_positions.items()):
            if current_ts > trade.window_end_ts:
                trade.exit_ts = now.isoformat()
                trade.exit_ts_epoch_ms = int(now.timestamp() * 1000)
                trade.payout = trade.tokens_acquired
                trade.gross_pnl = trade.payout - trade.entry_cost
                trade.net_pnl = trade.gross_pnl - trade.gas_cost
                trade.status = TradeStatus.CLOSED

                # Update Rust tracker (mark settled)
                # Note: Rust tracker doesn't persist, but we track in Python

                # Update equity
                self.pnl_summary.equity_in_positions -= trade.entry_cost
                self.pnl_summary.current_equity += trade.net_pnl
                self.pnl_summary.daily_pnl += trade.net_pnl
                self.pnl_summary.daily_trades += 1

                # Cumulative stats
                self.pnl_summary.total_trades += 1
                self.pnl_summary.total_volume += trade.trade_size_usd
                self.pnl_summary.total_gross_pnl += trade.gross_pnl
                self.pnl_summary.total_gas_paid += trade.gas_cost
                self.pnl_summary.total_net_pnl += trade.net_pnl

                if trade.net_pnl > 0:
                    self.pnl_summary.winning_trades += 1
                else:
                    self.pnl_summary.losing_trades += 1

                if trade.net_pnl > self.pnl_summary.best_trade_pnl:
                    self.pnl_summary.best_trade_pnl = trade.net_pnl
                if trade.net_pnl < self.pnl_summary.worst_trade_pnl:
                    self.pnl_summary.worst_trade_pnl = trade.net_pnl

                if self.pnl_summary.current_equity > self.pnl_summary.peak_equity:
                    self.pnl_summary.peak_equity = self.pnl_summary.current_equity
                drawdown = (self.pnl_summary.peak_equity - self.pnl_summary.current_equity) / self.pnl_summary.peak_equity
                if drawdown > self.pnl_summary.max_drawdown:
                    self.pnl_summary.max_drawdown = drawdown

                entry_margin = 1.0 - trade.entry_combined_ask
                n = self.pnl_summary.total_trades
                self.pnl_summary.avg_margin_at_entry = (
                    (self.pnl_summary.avg_margin_at_entry * (n - 1) + entry_margin) / n
                )

                self._save_trade(trade)
                self._save_pnl_summary()

                del self.open_positions[trade_id]
                closed.append(trade)

                self.logger.info(
                    f"CLOSED: {trade.trade_id} | {trade.coin} | "
                    f"Net=${trade.net_pnl:+.2f}"
                )

        return closed

    def get_status_line(self) -> str:
        """Get compact status line for logging."""
        open_count = len(self.open_positions)
        equity = self.pnl_summary.current_equity
        daily = self.pnl_summary.daily_pnl
        avg_latency = self.pnl_summary.avg_hot_path_latency_ms
        return (
            f"Equity=${equity:.0f} | "
            f"Today=${daily:+.2f} | "
            f"Net=${self.pnl_summary.total_net_pnl:+.2f} | "
            f"Open={open_count} | "
            f"AvgLatency={avg_latency:.1f}ms"
        )

    def get_full_summary(self) -> Dict:
        """Get full P&L summary as dict."""
        summary = asdict(self.pnl_summary)
        summary['return_pct'] = (
            (self.pnl_summary.current_equity - self.pnl_summary.initial_equity)
            / self.pnl_summary.initial_equity * 100
        )
        summary['win_rate'] = (
            self.pnl_summary.winning_trades / self.pnl_summary.total_trades * 100
            if self.pnl_summary.total_trades > 0 else 0
        )
        summary['available_equity'] = (
            self.pnl_summary.current_equity - self.pnl_summary.equity_in_positions
        )
        return summary


# =============================================================================
# ORDER BOOK PROCESSING
# =============================================================================

def parse_order_book(raw_book: Any, depth: int) -> OrderBook:
    bids = []
    asks = []
    if hasattr(raw_book, 'bids') and raw_book.bids:
        all_bids = [OrderLevel(price=float(b.price), size=float(b.size)) for b in raw_book.bids]
        all_bids.sort(key=lambda x: x.price, reverse=True)
        bids = all_bids[:depth]
    if hasattr(raw_book, 'asks') and raw_book.asks:
        all_asks = [OrderLevel(price=float(a.price), size=float(a.size)) for a in raw_book.asks]
        all_asks.sort(key=lambda x: x.price)
        asks = all_asks[:depth]
    best_bid = bids[0].price if bids else 0.0
    best_ask = asks[0].price if asks else 1.0
    spread = best_ask - best_bid
    return OrderBook(bids=bids, asks=asks, best_bid=best_bid, best_ask=best_ask, spread=spread)


# =============================================================================
# MARKET SCANNING
# =============================================================================

class MarketScanner:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.gamma = GammaClient()
        self.clob = ClobClient(host="https://clob.polymarket.com")
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers)
        self.token_cache: Dict[str, Dict] = {}
        self.cache_window_ts: int = 0

    def _get_window_timestamp(self) -> int:
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window = now.replace(minute=minute, second=0, microsecond=0)
        return int(window.timestamp())

    def _refresh_token_cache(self) -> None:
        current_window = self._get_window_timestamp()
        if current_window == self.cache_window_ts and self.token_cache:
            return
        self.logger.info(f"Refreshing token cache for window {current_window}")
        self.token_cache = {}
        for coin in self.config.coins:
            try:
                market = self.gamma.get_current_15m_market(coin)
                if market:
                    token_ids = self.gamma.parse_token_ids(market)
                    self.token_cache[coin] = {
                        "slug": market.get("slug", ""),
                        "end_date": market.get("endDate", ""),
                        "up_token": token_ids.get("up") or token_ids.get("yes"),
                        "down_token": token_ids.get("down") or token_ids.get("no")
                    }
            except Exception as e:
                self.logger.warning(f"Failed to get market for {coin}: {e}")
        self.cache_window_ts = current_window

    def _fetch_order_book(self, token_id: str) -> Optional[OrderBook]:
        try:
            raw = self.clob.get_order_book(token_id)
            return parse_order_book(raw, self.config.book_depth)
        except Exception:
            return None

    def scan_all_markets(self) -> List[Dict]:
        """Scan all markets and return simplified data for Rust processing."""
        self._refresh_token_cache()
        now = datetime.now(timezone.utc)
        results = []

        fetch_tasks = []
        for coin, cache in self.token_cache.items():
            if cache.get("up_token") and cache.get("down_token"):
                fetch_tasks.append((coin, "up", cache["up_token"]))
                fetch_tasks.append((coin, "down", cache["down_token"]))

        books: Dict[str, Dict[str, OrderBook]] = {}
        futures = {}
        for coin, side, token_id in fetch_tasks:
            future = self.executor.submit(self._fetch_order_book, token_id)
            futures[future] = (coin, side, token_id)

        for future in as_completed(futures):
            coin, side, token_id = futures[future]
            try:
                book = future.result()
                if book:
                    if coin not in books:
                        books[coin] = {}
                    books[coin][side] = book
            except Exception as e:
                self.logger.warning(f"Error fetching {coin} {side}: {e}")

        for coin in self.config.coins:
            if coin not in books or "up" not in books[coin] or "down" not in books[coin]:
                continue

            cache = self.token_cache.get(coin, {})
            up_book = books[coin]["up"]
            down_book = books[coin]["down"]

            end_str = cache.get("end_date", "")
            remaining_secs = 0.0
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    remaining_secs = (end_dt - now).total_seconds()
                except:
                    pass

            # Extract best level data
            up_best_size = up_book.asks[0].size if up_book.asks else 0
            down_best_size = down_book.asks[0].size if down_book.asks else 0

            results.append({
                "coin": coin,
                "slug": cache.get("slug", ""),
                "remaining_seconds": remaining_secs,
                "up_ask": up_book.best_ask,
                "down_ask": down_book.best_ask,
                "up_size": up_best_size,
                "down_size": down_best_size,
                "combined_ask": up_book.best_ask + down_book.best_ask,
                "up_book": {"bids": [asdict(l) for l in up_book.bids], "asks": [asdict(l) for l in up_book.asks]},
                "down_book": {"bids": [asdict(l) for l in down_book.bids], "asks": [asdict(l) for l in down_book.asks]}
            })

        return results

    def shutdown(self):
        self.executor.shutdown(wait=False)


# =============================================================================
# JSONL WRITER
# =============================================================================

class JSONLWriter:
    def __init__(self, base_dir: str, prefix: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.current_date: str = ""
        self.file_handle = None

    def _get_filename(self) -> Path:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self.base_dir / f"{self.prefix}_{date_str}.jsonl"

    def _ensure_file(self) -> None:
        current_date = datetime.now(timezone.utc).strftime("%Y%m%d")
        if current_date != self.current_date:
            if self.file_handle:
                self.file_handle.close()
            self.current_date = current_date
            filepath = self._get_filename()
            self.file_handle = open(filepath, "a", encoding="utf-8")

    def write(self, data: Dict) -> None:
        self._ensure_file()
        self.file_handle.write(json.dumps(data, separators=(',', ':')) + "\n")
        self.file_handle.flush()

    def close(self):
        if self.file_handle:
            self.file_handle.close()


# =============================================================================
# MAIN MONITOR LOOP
# =============================================================================

def get_window_info() -> tuple:
    now = datetime.now(timezone.utc)
    seconds_into_window = (now.minute % 15) * 60 + now.second
    seconds_remaining = 900 - seconds_into_window
    return seconds_into_window, seconds_remaining


def run_monitor(config: Config):
    logger = setup_logging(config.console_log)
    scanner = MarketScanner(config, logger)
    snapshot_writer = JSONLWriter(config.log_dir, "market_snapshots")
    opportunity_writer = JSONLWriter(config.log_dir, "opportunities")
    cleaner = DataCleaner(config.log_dir, config.retention_hours, config.cleanup_interval_mins, logger)
    paper_trader = RustPaperTrader(config, config.log_dir, logger)

    logger.info("=" * 70)
    logger.info("  GABAGOOL MONITOR v5-RUST - RUST HOT PATH INTEGRATION")
    logger.info("=" * 70)
    logger.info(f"Rust: {gabagool_rust.health_check()}")
    logger.info(f"Assets: {', '.join(config.coins)}")
    logger.info(f"Scan interval: {config.scan_interval_ms}ms")
    logger.info("-" * 70)
    logger.info("RUST HOT PATH:")
    logger.info("  - GabagoolStrategy: Arbitrage detection (<1ms)")
    logger.info("  - OrderExecutor: Paper execution (5-10ms)")
    logger.info("  - PositionTracker: Position management (<1ms)")
    logger.info("  - PriceFeedCache: Price caching (<1ms)")
    logger.info("  - Target total: <40ms (vs 380ms Python)")
    logger.info("-" * 70)
    logger.info("RISK MANAGEMENT:")
    logger.info(f"  Initial equity: ${config.initial_equity:.0f}")
    logger.info(f"  Daily loss limit: {config.daily_loss_limit_pct:.0%}")
    logger.info(f"  Min margin: {config.min_margin_to_trade:.2%}")
    logger.info("-" * 70)
    logger.info(f"Instance: '{config.instance}' | Files: {config.instance}_paper_*.json[l]")
    if config.fresh_start:
        logger.info("MODE: FRESH START")
    logger.info("=" * 70)
    logger.info(f"STATUS: {paper_trader.get_status_line()}")
    logger.info("=" * 70)
    logger.info("Press Ctrl+C to stop\n")

    cleaner.start()

    scan_count = 0
    opportunities_found = 0
    last_window_ts = 0

    try:
        while True:
            scan_start = time.time()
            scan_count += 1
            now = datetime.now(timezone.utc)
            secs_in, secs_remain = get_window_info()

            # Log window transitions
            current_window_ts = scanner._get_window_timestamp()
            if current_window_ts != last_window_ts:
                logger.info(f"\n>>> NEW WINDOW: {current_window_ts} <<<")
                logger.info(f">>> {paper_trader.get_status_line()} <<<\n")
                last_window_ts = current_window_ts

            # Check for position closures
            closed_trades = paper_trader.check_for_closures(current_window_ts)
            if closed_trades:
                for trade in closed_trades:
                    logger.info(f">>> RESOLVED: {trade.coin} Net=${trade.net_pnl:+.2f}")

            # Scan markets (Python)
            snapshots = scanner.scan_all_markets()
            scan_elapsed = time.time() - scan_start

            # Build record
            record = {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "ts_epoch_ms": int(now.timestamp() * 1000),
                "scan_id": scan_count,
                "scan_duration_ms": round(scan_elapsed * 1000, 1),
                "window": {"seconds_in": secs_in, "seconds_remain": secs_remain},
                "engine": "rust"
            }

            # Process opportunities using RUST hot path
            if snapshots:
                best = min(snapshots, key=lambda x: x["combined_ask"])
                record["best_opportunity"] = {
                    "coin": best["coin"],
                    "combined_ask": round(best["combined_ask"], 4)
                }

                for snap in snapshots:
                    if snap["combined_ask"] < config.opportunity_threshold:
                        opportunities_found += 1

                        # Log opportunity
                        opp_record = {
                            "ts": record["ts"],
                            "ts_epoch_ms": record["ts_epoch_ms"],
                            "scan_id": scan_count,
                            "coin": snap["coin"],
                            "slug": snap["slug"],
                            "combined_ask": round(snap["combined_ask"], 4),
                            "up_ask": snap["up_ask"],
                            "down_ask": snap["down_ask"],
                            "engine": "rust"
                        }
                        opportunity_writer.write(opp_record)

                        # Use RUST hot path for trade detection and execution
                        trade = paper_trader.check_for_opportunity_rust(
                            coin=snap["coin"],
                            slug=snap["slug"],
                            up_ask=snap["up_ask"],
                            down_ask=snap["down_ask"],
                            up_size=snap["up_size"],
                            down_size=snap["down_size"],
                            remaining_seconds=snap["remaining_seconds"],
                            full_record=record
                        )

            # Write snapshot
            snapshot_writer.write(record)

            # Console output
            best_str = f"{best['coin']}={best['combined_ask']:.3f}" if snapshots else "N/A"
            open_pos = len(paper_trader.open_positions)
            pnl = paper_trader.pnl_summary.total_net_pnl
            avg_latency = paper_trader.pnl_summary.avg_hot_path_latency_ms
            logger.info(
                f"#{scan_count} Best:{best_str} Scan:{scan_elapsed*1000:.0f}ms "
                f"Win:{secs_in}s/{secs_remain}s | Open:{open_pos} PnL:${pnl:+.2f} Lat:{avg_latency:.1f}ms"
            )

            # Sleep
            sleep_time = max(0, (config.scan_interval_ms / 1000) - scan_elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info(f"\n\n{'='*70}")
        logger.info("MONITOR STOPPED")
        logger.info(f"{'='*70}")
        logger.info(f"Scans: {scan_count}")
        logger.info(f"Opportunities: {opportunities_found}")

        summary = paper_trader.get_full_summary()
        logger.info(f"\nEQUITY:")
        logger.info(f"  Initial: ${summary['initial_equity']:.2f}")
        logger.info(f"  Current: ${summary['current_equity']:.2f}")
        logger.info(f"  Return: {summary['return_pct']:+.2f}%")

        logger.info(f"\nRUST PERFORMANCE:")
        logger.info(f"  Total executions: {summary['total_rust_executions']}")
        logger.info(f"  Avg hot path latency: {summary['avg_hot_path_latency_ms']:.2f}ms")

        logger.info(f"\nTRADES:")
        logger.info(f"  Total: {summary['total_trades']}")
        logger.info(f"  Win rate: {summary['win_rate']:.1f}%")
        logger.info(f"  Net P&L: ${summary['total_net_pnl']:+.2f}")

        logger.info(f"\nData: {config.log_dir}/")
        logger.info(f"{'='*70}")

    finally:
        cleaner.stop()
        scanner.shutdown()
        snapshot_writer.close()
        opportunity_writer.close()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Gabagool Monitor v5-Rust - Rust Hot Path Integration")

    parser.add_argument("--interval", type=int, default=500, help="Scan interval in ms")
    parser.add_argument("--depth", type=int, default=10, help="Order book depth")
    parser.add_argument("--threshold", type=float, default=1.00, help="Opportunity threshold")
    parser.add_argument("--equity", type=float, default=2000.0, help="Initial equity")
    parser.add_argument("--max-size", type=float, default=100.0, help="Max trade size")
    parser.add_argument("--max-positions", type=int, default=20, help="Max positions")
    parser.add_argument("--daily-loss-limit", type=float, default=0.20, help="Daily loss limit")
    parser.add_argument("--min-margin", type=float, default=0.005, help="Min margin")
    parser.add_argument("--gas-cost", type=float, default=0.003, help="Gas cost per tx")
    parser.add_argument("--retention-hours", type=int, default=8, help="Snapshot retention")
    parser.add_argument("--log-dir", type=str, default="data/logs", help="Output directory")
    parser.add_argument("--instance", type=str, default="rust_v5", help="Instance name")
    parser.add_argument("--fresh", action="store_true", help="Start fresh")

    args = parser.parse_args()

    config = Config(
        scan_interval_ms=args.interval,
        book_depth=args.depth,
        opportunity_threshold=args.threshold,
        initial_equity=args.equity,
        max_trade_size=args.max_size,
        max_open_positions=args.max_positions,
        daily_loss_limit_pct=args.daily_loss_limit,
        min_margin_to_trade=args.min_margin,
        gas_cost_per_tx=args.gas_cost,
        retention_hours=args.retention_hours,
        log_dir=args.log_dir,
        instance=args.instance,
        fresh_start=args.fresh
    )

    run_monitor(config)


if __name__ == "__main__":
    main()
