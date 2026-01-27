#!/usr/bin/env python3
"""
Gabagool 15-Minute Market Monitor v5 - Liquidity-First Stacking Strategy

Purpose:
    High-frequency order book capture with liquidity-aware paper trading.
    Eliminates slippage by only trading what's available at BEST price.
    Stacks multiple small trades instead of one large slippage-prone trade.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Strategy:
    - NO minimum trade size - take whatever liquidity exists at best ask
    - Stack multiple small trades on same opportunity (up to max_stack)
    - Only fill at best price level (zero slippage by design)
    - Fast responsiveness prioritized over large order sizes
    - Multiple trades per coin per window allowed

Features:
    - Always aggressive scanning (500ms intervals)
    - Liquidity-first sizing: trade size = min(available @ best, max_per_trade)
    - Trade stacking: multiple fills on same opportunity as liquidity refreshes
    - Auto-cleanup of snapshot data older than 8 hours
    - Real-time P&L tracking

Usage:
    python scripts/monitor_15m_v5.py

Output:
    - data/logs/market_snapshots_YYYYMMDD.jsonl (auto-deleted after 8 hours)
    - data/logs/opportunities_YYYYMMDD.jsonl (permanent)
    - data/logs/paper_trades.jsonl (permanent - all paper trades)
    - data/logs/paper_pnl_summary.json (cumulative P&L summary)
    - logs/monitor_v5.log (console mirror)
"""

import sys
import os
import time
import json
import glob
import threading
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any
from enum import Enum

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gamma_client import GammaClient
from py_clob_client.client import ClobClient


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    """Monitor configuration."""
    coins: List[str] = None
    scan_interval_ms: int = 500
    book_depth: int = 10
    sim_sizes: List[int] = None

    # Opportunity detection
    opportunity_threshold: float = 1.00  # combined_ask < this triggers opportunity

    # Paper trading - Liquidity-First Sizing (v5 strategy)
    initial_equity: float = 2000.0      # Starting capital
    max_trade_size: float = 100.0       # Max per single trade (capped by liquidity anyway)
    max_position_pct: float = 0.25      # Max 25% of equity across all positions
    gas_cost_per_tx: float = 0.003      # $ per transaction (Polygon realistic)
    min_margin_to_trade: float = 0.005  # Minimum margin (0.5%) to open trade

    # Stacking - Multiple trades per opportunity
    max_stack_per_window: int = 10      # Max trades per coin per 15-min window
    min_seconds_between_stack: float = 0.0  # No cooldown - trade as fast as liquidity appears

    # Risk Management
    daily_loss_limit_pct: float = 0.20  # 20% max daily loss
    max_open_positions: int = 20        # Allow many small positions (stacking)

    # NO minimum trade size - trade whatever liquidity exists at best price
    # NO liquidity multiple requirement - we only take what's at best level

    # Data retention
    retention_hours: int = 8
    cleanup_interval_mins: int = 15

    # Output paths
    log_dir: str = "data/logs"
    console_log: str = "logs/monitor_v5.log"
    max_workers: int = 8

    # Instance isolation - allows multiple paper trading instances
    instance: str = "default"           # Instance name for separate paper trading
    fresh_start: bool = False           # If True, ignore existing state and start fresh

    def __post_init__(self):
        if self.coins is None:
            self.coins = ["BTC", "ETH", "SOL", "XRP"]
        if self.sim_sizes is None:
            self.sim_sizes = [10, 25, 50, 100, 200]  # Smaller sizes for stacking strategy


# =============================================================================
# DATA STRUCTURES
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

@dataclass
class TokenSnapshot:
    token_id: str
    best_bid: float
    best_ask: float
    spread: float
    book: Dict[str, List[Dict]]
    depth_prices: Dict[str, float]

@dataclass
class MarketSnapshot:
    coin: str
    slug: str
    remaining_seconds: float
    up: TokenSnapshot
    down: TokenSnapshot
    combined_ask: float
    combined_bid: float
    margin: float
    execution_sim: Dict[int, Dict]


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"  # Market ended without resolution data


@dataclass
class PaperTrade:
    """Represents a paper trade."""
    trade_id: str
    coin: str
    slug: str
    window_end_ts: int              # Unix timestamp when market resolves

    # Entry
    entry_ts: str                   # ISO timestamp
    entry_ts_epoch_ms: int
    entry_combined_ask: float
    entry_up_ask: float
    entry_down_ask: float
    entry_up_size: float            # Size available at entry
    entry_down_size: float

    # Position
    trade_size_usd: float           # $ invested
    tokens_acquired: float          # Number of token pairs
    entry_cost: float               # Actual cost with slippage
    gas_cost: float                 # Gas for entry

    # Exit (filled on close)
    exit_ts: Optional[str] = None
    exit_ts_epoch_ms: Optional[int] = None
    payout: Optional[float] = None
    gross_pnl: Optional[float] = None
    net_pnl: Optional[float] = None

    status: TradeStatus = TradeStatus.OPEN

    # Snapshot of market state at entry (for analysis)
    entry_snapshot: Optional[Dict] = None


@dataclass
class PnLSummary:
    """Cumulative P&L tracking with equity management."""
    # Equity
    initial_equity: float = 2000.0
    current_equity: float = 2000.0
    equity_in_positions: float = 0.0    # $ currently locked in open positions

    # Daily tracking (resets each day)
    daily_start_equity: float = 2000.0
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_date: str = ""                # YYYY-MM-DD

    # Cumulative stats
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

    # Risk tracking
    max_drawdown: float = 0.0
    peak_equity: float = 2000.0
    trading_halted: bool = False        # True if daily loss limit hit
    halt_reason: str = ""

    last_updated: str = ""


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(console_log_path: str) -> logging.Logger:
    """Setup dual logging to console and file."""
    logger = logging.getLogger("monitor_v5")
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

    def _truncate_current_file(self) -> int:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        current_file = self.log_dir / f"market_snapshots_{today}.jsonl"
        if not current_file.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        try:
            with open(current_file, 'r') as f:
                lines = f.readlines()
            original_count = len(lines)
            kept_lines = []
            for line in lines:
                try:
                    record = json.loads(line)
                    if record.get('ts_epoch_ms', 0) >= cutoff_ms:
                        kept_lines.append(line)
                except json.JSONDecodeError:
                    kept_lines.append(line)

            if len(kept_lines) < original_count:
                with open(current_file, 'w') as f:
                    f.writelines(kept_lines)
                deleted = original_count - len(kept_lines)
                self.logger.info(f"Truncated {deleted} old records from {current_file.name}")
                return deleted
        except Exception as e:
            self.logger.warning(f"Failed to truncate {current_file}: {e}")
        return 0

    def _cleanup_loop(self):
        while not self._stop_event.wait(self.cleanup_interval_mins * 60):
            try:
                deleted_files = self._cleanup_old_files()
                truncated_records = self._truncate_current_file()
                if deleted_files > 0 or truncated_records > 0:
                    self.logger.info(f"Cleanup: {deleted_files} files, {truncated_records} records removed")
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
# PAPER TRADER
# =============================================================================

class PaperTrader:
    """
    Paper trading system for arbitrage simulation - v5 Stacking Strategy.

    Features:
    - NO minimum trade size - takes whatever liquidity at best price
    - Stacking: multiple small trades per opportunity
    - Only trades at best ask level (zero slippage by design)
    - Equity tracking with $2k initial capital
    - 20% daily loss limit
    - Opens positions when combined_ask < threshold
    - Closes positions when market resolves (15-min window ends)
    """

    def __init__(self, config: Config, log_dir: str, logger: logging.Logger):
        self.config = config
        self.log_dir = Path(log_dir)
        self.logger = logger
        self.instance = config.instance

        # Open positions: trade_id -> PaperTrade (allows multiple per coin/slug)
        self.open_positions: Dict[str, PaperTrade] = {}

        # Stacking tracking: coin -> {window_ts -> count, last_trade_time}
        self.stack_counts: Dict[str, Dict[int, int]] = {}  # coin -> {window_ts -> count}
        self.last_trade_time: Dict[str, float] = {}  # coin -> epoch timestamp

        # Trade counter for unique IDs
        self.trade_counter = 0

        # P&L summary with equity
        self.pnl_summary = PnLSummary(
            initial_equity=config.initial_equity,
            current_equity=config.initial_equity,
            daily_start_equity=config.initial_equity,
            peak_equity=config.initial_equity
        )

        # File paths with instance prefix (allows multiple independent paper traders)
        # e.g., "v5_paper_trades.jsonl" or "aggressive_paper_trades.jsonl"
        self.trades_file = self.log_dir / f"{self.instance}_paper_trades.jsonl"
        self.pnl_file = self.log_dir / f"{self.instance}_paper_pnl_summary.json"

        # Load existing state (unless fresh_start)
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
                    self.pnl_summary = PnLSummary(**data)
                    self.trade_counter = self.pnl_summary.total_trades
                    self.logger.info(
                        f"Loaded P&L: {self.pnl_summary.total_trades} trades, "
                        f"Equity=${self.pnl_summary.current_equity:.2f}, "
                        f"Net=${self.pnl_summary.total_net_pnl:.2f}"
                    )
            except Exception as e:
                self.logger.warning(f"Failed to load P&L summary: {e}")

        # Load open positions from trades file (keyed by trade_id for stacking)
        if self.trades_file.exists():
            try:
                with open(self.trades_file, 'r') as f:
                    for line in f:
                        trade = json.loads(line)
                        if trade.get('status') == 'OPEN':
                            trade_obj = self._dict_to_trade(trade)
                            self.open_positions[trade_obj.trade_id] = trade_obj
                if self.open_positions:
                    self.logger.info(f"Restored {len(self.open_positions)} open positions")
                    # Recalculate equity in positions
                    self.pnl_summary.equity_in_positions = sum(
                        t.entry_cost for t in self.open_positions.values()
                    )
            except Exception as e:
                self.logger.warning(f"Failed to load open positions: {e}")

        # Check for daily reset
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
        return f"PT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{self.trade_counter:05d}"

    def _get_window_end_timestamp(self, slug: str) -> int:
        """Extract window end timestamp from slug."""
        # Slug format: btc-updown-15m-1769494500
        try:
            parts = slug.split('-')
            return int(parts[-1]) + 900  # Add 15 minutes
        except:
            return 0

    def _check_daily_reset(self):
        """Check if we need to reset daily stats (new day)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.pnl_summary.daily_date != today:
            if self.pnl_summary.daily_date:  # Not first run
                self.logger.info(f"New day: resetting daily stats (previous: ${self.pnl_summary.daily_pnl:+.2f})")
            self.pnl_summary.daily_date = today
            self.pnl_summary.daily_start_equity = self.pnl_summary.current_equity
            self.pnl_summary.daily_pnl = 0.0
            self.pnl_summary.daily_trades = 0
            self.pnl_summary.trading_halted = False
            self.pnl_summary.halt_reason = ""
            self._save_pnl_summary()

    def _calculate_liquidity_position_size(self, snapshot: 'MarketSnapshot') -> float:
        """
        Calculate position size based on LIQUIDITY AT BEST PRICE ONLY.

        v5 Strategy: Zero slippage by design
        - Only trade what's available at best ask on BOTH sides
        - No minimum size - take whatever is there
        - Cap at max_trade_size and available equity

        Returns trade size in USD (can be very small or zero)
        """
        available_equity = self.pnl_summary.current_equity - self.pnl_summary.equity_in_positions

        if available_equity <= 0:
            return 0

        # Get liquidity at best ask for both sides
        up_best_size = 0
        down_best_size = 0

        if snapshot.up.book.get('asks'):
            level = snapshot.up.book['asks'][0]
            up_best_size = level.get('size', 0) * level.get('price', 0)  # $ available

        if snapshot.down.book.get('asks'):
            level = snapshot.down.book['asks'][0]
            down_best_size = level.get('size', 0) * level.get('price', 0)  # $ available

        if up_best_size <= 0 or down_best_size <= 0:
            return 0

        # The limiting factor is the side with less liquidity
        # We need to buy both sides, so max trade = min(up_liquidity, down_liquidity) * 2
        # Actually: if UP has $10 and DOWN has $20, we can only buy $10 worth of each
        # So total trade = min(up, down) * 2 in terms of combined cost
        min_side_liquidity = min(up_best_size, down_best_size)

        # Convert to total trade size (buying both sides)
        # At combined_ask = 0.99, $10 on each side means ~$20 total cost
        liquidity_limited_size = min_side_liquidity * 2

        # Apply caps
        max_size = min(
            self.config.max_trade_size,
            available_equity,
            liquidity_limited_size
        )

        return max_size if max_size > 0 else 0

    def _can_stack_trade(self, coin: str, window_ts: int) -> tuple:
        """
        Check if we can stack another trade on this coin/window.

        Returns (can_stack: bool, reason: str)
        """
        # Check stack count for this window
        if coin not in self.stack_counts:
            self.stack_counts[coin] = {}

        current_count = self.stack_counts[coin].get(window_ts, 0)
        if current_count >= self.config.max_stack_per_window:
            return False, f"max_stack_reached ({current_count}/{self.config.max_stack_per_window})"

        # Check cooldown
        last_trade = self.last_trade_time.get(coin, 0)
        elapsed = time.time() - last_trade
        if elapsed < self.config.min_seconds_between_stack:
            return False, f"cooldown ({elapsed:.1f}s < {self.config.min_seconds_between_stack}s)"

        return True, "ok"

    def _record_stack_trade(self, coin: str, window_ts: int):
        """Record that we stacked a trade."""
        if coin not in self.stack_counts:
            self.stack_counts[coin] = {}
        self.stack_counts[coin][window_ts] = self.stack_counts[coin].get(window_ts, 0) + 1
        self.last_trade_time[coin] = time.time()

    def _check_risk_limits(self) -> tuple:
        """
        Check if we can trade based on risk limits.

        Returns (can_trade: bool, reason: str)
        """
        # Check if trading is halted
        if self.pnl_summary.trading_halted:
            return False, f"trading_halted: {self.pnl_summary.halt_reason}"

        # Check daily loss limit
        daily_loss_limit = self.pnl_summary.daily_start_equity * self.config.daily_loss_limit_pct
        if self.pnl_summary.daily_pnl < -daily_loss_limit:
            self.pnl_summary.trading_halted = True
            self.pnl_summary.halt_reason = f"daily_loss_limit (${self.pnl_summary.daily_pnl:.2f})"
            self._save_pnl_summary()
            self.logger.warning(f"TRADING HALTED: Daily loss limit hit (${self.pnl_summary.daily_pnl:.2f})")
            return False, self.pnl_summary.halt_reason

        # Check max open positions
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, f"max_positions ({len(self.open_positions)}/{self.config.max_open_positions})"

        # Check minimum equity (need at least $1 to trade anything)
        available = self.pnl_summary.current_equity - self.pnl_summary.equity_in_positions
        if available < 1.0:
            return False, f"insufficient_equity (${available:.2f} available)"

        return True, "ok"

    def check_for_opportunity(self, snapshot: MarketSnapshot, full_record: Dict) -> Optional[PaperTrade]:
        """
        Check if snapshot represents a trade opportunity.

        v5 Stacking Strategy:
        1. Combined ask below threshold
        2. Margin above minimum
        3. Enough time remaining
        4. Risk limits (daily loss, max positions, equity)
        5. Can stack (not at max, cooldown passed)
        6. Calculate liquidity-first position size (NO slippage)

        Returns PaperTrade if opportunity detected and trade opened, None otherwise.
        """
        # Check for daily reset
        self._check_daily_reset()

        # Check threshold
        if snapshot.combined_ask >= self.config.opportunity_threshold:
            return None

        # Check minimum margin
        margin = 1.0 - snapshot.combined_ask
        if margin < self.config.min_margin_to_trade:
            return None

        # Check if market has enough time remaining (at least 30 seconds)
        if snapshot.remaining_seconds < 30:
            return None

        # Check risk limits (daily loss, max positions, etc.)
        can_trade, risk_reason = self._check_risk_limits()
        if not can_trade:
            self.logger.debug(f"Skipping {snapshot.coin}: {risk_reason}")
            return None

        # Extract window timestamp for stacking
        window_ts = self._get_window_end_timestamp(snapshot.slug) - 900  # Start of window

        # Check if we can stack another trade
        can_stack, stack_reason = self._can_stack_trade(snapshot.coin, window_ts)
        if not can_stack:
            return None

        # Calculate position size based on liquidity at BEST PRICE ONLY (no slippage)
        trade_size = self._calculate_liquidity_position_size(snapshot)
        if trade_size < 1.0:  # Need at least $1
            return None

        # For v5: entry_cost = trade_size (no slippage, we only take best level)
        # Tokens acquired = trade_size / combined_ask
        entry_cost = trade_size
        tokens_acquired = trade_size / snapshot.combined_ask

        # Gas: 2 transactions (buy UP + buy DOWN)
        gas_cost = self.config.gas_cost_per_tx * 2

        now = datetime.now(timezone.utc)

        trade = PaperTrade(
            trade_id=self._generate_trade_id(),
            coin=snapshot.coin,
            slug=snapshot.slug,
            window_end_ts=self._get_window_end_timestamp(snapshot.slug),
            entry_ts=now.isoformat(),
            entry_ts_epoch_ms=int(now.timestamp() * 1000),
            entry_combined_ask=snapshot.combined_ask,
            entry_up_ask=snapshot.up.best_ask,
            entry_down_ask=snapshot.down.best_ask,
            entry_up_size=snapshot.up.book['asks'][0]['size'] if snapshot.up.book['asks'] else 0,
            entry_down_size=snapshot.down.book['asks'][0]['size'] if snapshot.down.book['asks'] else 0,
            trade_size_usd=trade_size,
            tokens_acquired=tokens_acquired,
            entry_cost=entry_cost,
            gas_cost=gas_cost,
            status=TradeStatus.OPEN,
            entry_snapshot={
                'scan_id': full_record.get('scan_id'),
                'window_seconds_remain': snapshot.remaining_seconds,
                'strategy': 'liquidity_first_no_slippage',
                'margin': margin,
                'equity_before': self.pnl_summary.current_equity,
                'position_size_reason': f"best_level_liquidity -> ${trade_size:.2f}",
            }
        )

        # Update equity tracking
        self.pnl_summary.equity_in_positions += entry_cost

        # Store and save (keyed by trade_id to allow stacking)
        self.open_positions[trade.trade_id] = trade
        self._record_stack_trade(snapshot.coin, window_ts)
        self._save_trade(trade)
        self._save_pnl_summary()

        stack_count = self.stack_counts.get(snapshot.coin, {}).get(window_ts, 0)
        self.logger.info(
            f"PAPER TRADE OPENED: {trade.trade_id} | {snapshot.coin} | "
            f"Combined={snapshot.combined_ask:.4f} | Margin={margin:.2%} | "
            f"Size=${trade_size:.2f} -> {tokens_acquired:.2f} tokens | "
            f"Stack={stack_count}/{self.config.max_stack_per_window} | "
            f"Equity=${self.pnl_summary.current_equity:.0f}"
        )

        return trade

    def check_for_closures(self, current_window_ts: int) -> List[PaperTrade]:
        """
        Check if any open positions should be closed.

        Markets resolve at window end. We close positions when:
        - Current window timestamp > trade's window end timestamp

        Returns list of closed trades.
        """
        closed = []
        now = datetime.now(timezone.utc)
        current_ts = int(now.timestamp())

        for trade_id, trade in list(self.open_positions.items()):
            # Check if market has resolved (current time > window end)
            if current_ts > trade.window_end_ts:
                # Market resolved - close position
                trade.exit_ts = now.isoformat()
                trade.exit_ts_epoch_ms = int(now.timestamp() * 1000)

                # Payout is guaranteed $1 per token pair (one side wins)
                trade.payout = trade.tokens_acquired
                trade.gross_pnl = trade.payout - trade.entry_cost
                trade.net_pnl = trade.gross_pnl - trade.gas_cost
                trade.status = TradeStatus.CLOSED

                # Update equity - release locked funds + add P&L
                self.pnl_summary.equity_in_positions -= trade.entry_cost
                self.pnl_summary.current_equity += trade.net_pnl

                # Update daily P&L
                self.pnl_summary.daily_pnl += trade.net_pnl
                self.pnl_summary.daily_trades += 1

                # Update cumulative stats
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

                # Update peak equity and drawdown
                if self.pnl_summary.current_equity > self.pnl_summary.peak_equity:
                    self.pnl_summary.peak_equity = self.pnl_summary.current_equity
                drawdown = (self.pnl_summary.peak_equity - self.pnl_summary.current_equity) / self.pnl_summary.peak_equity
                if drawdown > self.pnl_summary.max_drawdown:
                    self.pnl_summary.max_drawdown = drawdown

                # Update average margin
                entry_margin = 1.0 - trade.entry_combined_ask
                n = self.pnl_summary.total_trades
                self.pnl_summary.avg_margin_at_entry = (
                    (self.pnl_summary.avg_margin_at_entry * (n - 1) + entry_margin) / n
                )

                # Save
                self._save_trade(trade)
                self._save_pnl_summary()

                # Remove from open positions
                del self.open_positions[trade_id]
                closed.append(trade)

                self.logger.info(
                    f"PAPER TRADE CLOSED: {trade.trade_id} | {trade.coin} | "
                    f"Payout=${trade.payout:.2f} | Gross=${trade.gross_pnl:+.2f} | "
                    f"Gas=${trade.gas_cost:.2f} | Net=${trade.net_pnl:+.2f}"
                )

        return closed

    def get_status_line(self) -> str:
        """Get compact status line for logging."""
        open_count = len(self.open_positions)
        equity = self.pnl_summary.current_equity
        daily = self.pnl_summary.daily_pnl
        return (
            f"Equity=${equity:.0f} | "
            f"Today=${daily:+.2f} | "
            f"Net=${self.pnl_summary.total_net_pnl:+.2f} | "
            f"Open={open_count}"
        )

    def get_full_summary(self) -> Dict:
        """Get full P&L summary as dict."""
        summary = asdict(self.pnl_summary)
        # Add computed fields
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
    # Parse ALL levels first, then sort, then slice to depth
    # API returns asks in descending price order, we need ascending (best price first)
    if hasattr(raw_book, 'bids') and raw_book.bids:
        all_bids = [OrderLevel(price=float(b.price), size=float(b.size)) for b in raw_book.bids]
        all_bids.sort(key=lambda x: x.price, reverse=True)  # Highest first for bids
        bids = all_bids[:depth]
    if hasattr(raw_book, 'asks') and raw_book.asks:
        all_asks = [OrderLevel(price=float(a.price), size=float(a.size)) for a in raw_book.asks]
        all_asks.sort(key=lambda x: x.price)  # Lowest first for asks
        asks = all_asks[:depth]
    best_bid = bids[0].price if bids else 0.0
    best_ask = asks[0].price if asks else 1.0
    spread = best_ask - best_bid
    return OrderBook(bids=bids, asks=asks, best_bid=best_bid, best_ask=best_ask, spread=spread)


def calculate_depth_price(book: OrderBook, target_size: float, side: str = "ask") -> float:
    levels = book.asks if side == "ask" else book.bids
    if not levels:
        return 0.0
    remaining = target_size
    total_cost = 0.0
    total_filled = 0.0
    for level in levels:
        level_value = level.size * level.price
        if remaining <= level_value:
            tokens_needed = remaining / level.price
            total_cost += remaining
            total_filled += tokens_needed
            remaining = 0
            break
        else:
            total_cost += level_value
            total_filled += level.size
            remaining -= level_value
    if remaining > 0:
        return 0.0
    return total_cost / total_filled if total_filled > 0 else 0.0


def simulate_execution(up_book: OrderBook, down_book: OrderBook, size: float) -> Dict:
    combined_ask = up_book.best_ask + down_book.best_ask
    if combined_ask <= 0:
        return {"cost": size, "avg_price": 0, "profit": -size, "tokens": 0}
    tokens = size / combined_ask
    up_fill_price = calculate_depth_price(up_book, tokens * up_book.best_ask, "ask")
    down_fill_price = calculate_depth_price(down_book, tokens * down_book.best_ask, "ask")
    if up_fill_price == 0 or down_fill_price == 0:
        return {"cost": size, "avg_price": 0, "profit": -size, "tokens": 0, "error": "insufficient_liquidity"}
    actual_combined = up_fill_price + down_fill_price
    actual_cost = tokens * actual_combined
    payout = tokens
    profit = payout - actual_cost
    return {
        "cost": round(actual_cost, 4),
        "avg_price": round(actual_combined, 4),
        "profit": round(profit, 4),
        "tokens": round(tokens, 2)
    }


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

    def scan_all_markets(self) -> List[MarketSnapshot]:
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
                    books[coin][f"{side}_token"] = token_id
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
            up_snapshot = TokenSnapshot(
                token_id=books[coin].get("up_token", ""),
                best_bid=up_book.best_bid,
                best_ask=up_book.best_ask,
                spread=up_book.spread,
                book={"bids": [asdict(l) for l in up_book.bids], "asks": [asdict(l) for l in up_book.asks]},
                depth_prices={f"depth_{s}": calculate_depth_price(up_book, s, "ask") for s in self.config.sim_sizes}
            )
            down_snapshot = TokenSnapshot(
                token_id=books[coin].get("down_token", ""),
                best_bid=down_book.best_bid,
                best_ask=down_book.best_ask,
                spread=down_book.spread,
                book={"bids": [asdict(l) for l in down_book.bids], "asks": [asdict(l) for l in down_book.asks]},
                depth_prices={f"depth_{s}": calculate_depth_price(down_book, s, "ask") for s in self.config.sim_sizes}
            )
            combined_ask = up_book.best_ask + down_book.best_ask
            combined_bid = up_book.best_bid + down_book.best_bid
            margin = 1.0 - combined_ask
            exec_sim = {}
            for size in self.config.sim_sizes:
                exec_sim[size] = simulate_execution(up_book, down_book, size)
            results.append(MarketSnapshot(
                coin=coin, slug=cache.get("slug", ""), remaining_seconds=remaining_secs,
                up=up_snapshot, down=down_snapshot,
                combined_ask=combined_ask, combined_bid=combined_bid, margin=margin,
                execution_sim=exec_sim
            ))
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


def snapshot_to_dict(snapshot: MarketSnapshot) -> Dict:
    return {
        "coin": snapshot.coin,
        "slug": snapshot.slug,
        "remaining_seconds": round(snapshot.remaining_seconds, 1),
        "up": {
            "token_id": snapshot.up.token_id,
            "best_bid": snapshot.up.best_bid,
            "best_ask": snapshot.up.best_ask,
            "spread": round(snapshot.up.spread, 4),
            "book": snapshot.up.book,
            "depth_prices": snapshot.up.depth_prices
        },
        "down": {
            "token_id": snapshot.down.token_id,
            "best_bid": snapshot.down.best_bid,
            "best_ask": snapshot.down.best_ask,
            "spread": round(snapshot.down.spread, 4),
            "book": snapshot.down.book,
            "depth_prices": snapshot.down.depth_prices
        },
        "combined": {
            "best_ask": round(snapshot.combined_ask, 4),
            "best_bid": round(snapshot.combined_bid, 4),
            "margin": round(snapshot.margin, 4)
        },
        "execution_sim": snapshot.execution_sim
    }


def run_monitor(config: Config):
    logger = setup_logging(config.console_log)
    scanner = MarketScanner(config, logger)
    snapshot_writer = JSONLWriter(config.log_dir, "market_snapshots")
    opportunity_writer = JSONLWriter(config.log_dir, "opportunities")
    cleaner = DataCleaner(config.log_dir, config.retention_hours, config.cleanup_interval_mins, logger)
    paper_trader = PaperTrader(config, config.log_dir, logger)

    logger.info("=" * 70)
    logger.info("  GABAGOOL MONITOR v5 - LIQUIDITY-FIRST STACKING STRATEGY")
    logger.info("=" * 70)
    logger.info(f"Assets: {', '.join(config.coins)}")
    logger.info(f"Scan interval: {config.scan_interval_ms}ms (always aggressive)")
    logger.info(f"Opportunity threshold: combined_ask < {config.opportunity_threshold}")
    logger.info("-" * 70)
    logger.info("v5 STRATEGY: Zero Slippage by Design")
    logger.info("  - NO minimum trade size - take whatever liquidity exists")
    logger.info("  - Only trade at BEST ask level (zero slippage)")
    logger.info("  - Stack multiple small trades per opportunity")
    logger.info("-" * 70)
    logger.info("STACKING CONFIG:")
    logger.info(f"  Max trade size: ${config.max_trade_size:.0f} (capped at best-level liquidity)")
    logger.info(f"  Max stack per window: {config.max_stack_per_window} trades/coin")
    logger.info(f"  Stack cooldown: {config.min_seconds_between_stack}s between trades")
    logger.info(f"  Max open positions: {config.max_open_positions}")
    logger.info("-" * 70)
    logger.info("RISK MANAGEMENT:")
    logger.info(f"  Initial equity: ${config.initial_equity:.0f}")
    logger.info(f"  Daily loss limit: {config.daily_loss_limit_pct:.0%} (${config.initial_equity * config.daily_loss_limit_pct:.0f})")
    logger.info(f"  Min margin to trade: {config.min_margin_to_trade:.2%}")
    logger.info(f"  Gas cost: ${config.gas_cost_per_tx}/tx")
    logger.info("-" * 70)
    logger.info(f"Data retention: {config.retention_hours}h | Output: {config.log_dir}/")
    logger.info(f"Instance: '{config.instance}' | Files: {config.instance}_paper_*.json[l]")
    if config.fresh_start:
        logger.info("MODE: FRESH START (ignoring previous state)")
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

            # Check for position closures (market resolutions)
            closed_trades = paper_trader.check_for_closures(current_window_ts)
            if closed_trades:
                for trade in closed_trades:
                    logger.info(f">>> POSITION RESOLVED: {trade.coin} Net P&L: ${trade.net_pnl:+.2f}")

            # Scan markets
            snapshots = scanner.scan_all_markets()
            scan_elapsed = time.time() - scan_start

            # Build record
            record = {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "ts_epoch_ms": int(now.timestamp() * 1000),
                "scan_id": scan_count,
                "scan_duration_ms": round(scan_elapsed * 1000, 1),
                "window": {"seconds_in": secs_in, "seconds_remain": secs_remain},
                "markets": [snapshot_to_dict(s) for s in snapshots]
            }

            # Find best opportunity
            if snapshots:
                best = min(snapshots, key=lambda x: x.combined_ask)
                record["best_opportunity"] = {
                    "coin": best.coin,
                    "combined_ask": round(best.combined_ask, 4),
                    "margin": round(best.margin, 4)
                }

                # Check for opportunities and paper trades
                for snap in snapshots:
                    if snap.combined_ask < config.opportunity_threshold:
                        opportunities_found += 1

                        # Log opportunity
                        opp_record = {
                            "ts": record["ts"],
                            "ts_epoch_ms": record["ts_epoch_ms"],
                            "scan_id": scan_count,
                            "coin": snap.coin,
                            "slug": snap.slug,
                            "combined_ask": round(snap.combined_ask, 4),
                            "margin": round(snap.margin, 4),
                            "up_ask": snap.up.best_ask,
                            "down_ask": snap.down.best_ask,
                            "up_size_at_best": snap.up.book["asks"][0]["size"] if snap.up.book["asks"] else 0,
                            "down_size_at_best": snap.down.book["asks"][0]["size"] if snap.down.book["asks"] else 0,
                            "remaining_seconds": snap.remaining_seconds,
                            "execution_sim": snap.execution_sim
                        }
                        opportunity_writer.write(opp_record)

                        # Try to open paper trade
                        trade = paper_trader.check_for_opportunity(snap, record)
                        if trade:
                            logger.info(f">>> OPPORTUNITY + TRADE: {snap.coin} Combined={snap.combined_ask:.4f}")
                        else:
                            logger.info(f">>> OPPORTUNITY: {snap.coin} Combined={snap.combined_ask:.4f} (no trade)")

            # Write snapshot
            snapshot_writer.write(record)

            # Console output
            best_str = f"{best.coin}={best.combined_ask:.3f}" if snapshots else "N/A"
            open_pos = len(paper_trader.open_positions)
            pnl = paper_trader.pnl_summary.total_net_pnl
            logger.info(
                f"#{scan_count} Best:{best_str} Scan:{scan_elapsed*1000:.0f}ms "
                f"Win:{secs_in}s/{secs_remain}s | Open:{open_pos} PnL:${pnl:+.2f}"
            )

            # Sleep
            sleep_time = max(0, (config.scan_interval_ms / 1000) - scan_elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info(f"\n\n{'='*70}")
        logger.info("MONITOR STOPPED")
        logger.info(f"{'='*70}")
        logger.info(f"Scans: {scan_count}")
        logger.info(f"Opportunities detected: {opportunities_found}")

        summary = paper_trader.get_full_summary()
        logger.info(f"\nEQUITY STATUS:")
        logger.info(f"  Initial: ${summary['initial_equity']:.2f}")
        logger.info(f"  Current: ${summary['current_equity']:.2f}")
        logger.info(f"  Return: {summary['return_pct']:+.2f}%")
        logger.info(f"  Max Drawdown: {summary['max_drawdown']:.2%}")
        logger.info(f"  Today's P&L: ${summary['daily_pnl']:+.2f}")

        logger.info(f"\nTRADING SUMMARY:")
        logger.info(f"  Total trades: {summary['total_trades']}")
        logger.info(f"  Win rate: {summary['win_rate']:.1f}% ({summary['winning_trades']}W / {summary['losing_trades']}L)")
        logger.info(f"  Total volume: ${summary['total_volume']:.2f}")
        logger.info(f"  Gross P&L: ${summary['total_gross_pnl']:+.2f}")
        logger.info(f"  Gas paid: ${summary['total_gas_paid']:.2f}")
        logger.info(f"  Net P&L: ${summary['total_net_pnl']:+.2f}")
        logger.info(f"  Best trade: ${summary['best_trade_pnl']:+.2f}")
        logger.info(f"  Worst trade: ${summary['worst_trade_pnl']:+.2f}")
        logger.info(f"  Avg margin at entry: {summary['avg_margin_at_entry']:.2%}")

        if summary['trading_halted']:
            logger.info(f"\n  WARNING: Trading was halted - {summary['halt_reason']}")

        logger.info(f"\nData saved to: {config.log_dir}/")
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
    parser = argparse.ArgumentParser(description="Gabagool Monitor v5 - Liquidity-First Stacking Strategy")

    # Scan settings
    parser.add_argument("--interval", type=int, default=500, help="Scan interval in ms (default: 500)")
    parser.add_argument("--depth", type=int, default=10, help="Order book depth (default: 10)")
    parser.add_argument("--threshold", type=float, default=1.00, help="Opportunity threshold (default: 1.00)")

    # Equity & position sizing (v5: no minimum, liquidity-first)
    parser.add_argument("--equity", type=float, default=2000.0, help="Initial equity in $ (default: 2000)")
    parser.add_argument("--max-size", type=float, default=100.0, help="Max trade size in $ (default: 100)")
    parser.add_argument("--max-positions", type=int, default=20, help="Max concurrent positions (default: 20)")

    # Risk management
    parser.add_argument("--daily-loss-limit", type=float, default=0.20, help="Daily loss limit as decimal (default: 0.20)")
    parser.add_argument("--min-margin", type=float, default=0.005, help="Min margin to trade (default: 0.005)")
    parser.add_argument("--gas-cost", type=float, default=0.003, help="Gas cost per tx in $ (default: 0.003)")

    # Data management
    parser.add_argument("--retention-hours", type=int, default=8, help="Snapshot retention hours (default: 8)")
    parser.add_argument("--log-dir", type=str, default="data/logs", help="Output directory (default: data/logs)")

    # Instance isolation (for running multiple paper traders)
    parser.add_argument("--instance", type=str, default="v5", help="Instance name for paper trading isolation (default: v5)")
    parser.add_argument("--fresh", action="store_true", help="Start fresh, ignore existing paper trading state")

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
