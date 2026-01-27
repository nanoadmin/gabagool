#!/usr/bin/env python3
"""
Gabagool 15-Minute Market Monitor v3 - Comprehensive Order Book Logger

Purpose:
    High-frequency order book capture for arbitrage analysis and backtesting.
    Logs full order book depth in JSONL format for offline analysis.

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Performance Characteristics:
    - API latency: ~180-200ms per order book call
    - Parallel fetching: 8 calls in ~200-300ms
    - Theoretical max: ~3 scans/second (limited by API latency)
    - Practical max: ~2 scans/second (with overhead + safety margin)
    - Recommended: 500ms-1000ms intervals for aggressive mode

Usage:
    python scripts/monitor_15m_v3.py [--interval 500] [--depth 10]

Output:
    - data/logs/market_snapshots_YYYYMMDD.jsonl (main data)
    - data/logs/opportunities_YYYYMMDD.jsonl (when combined < threshold)
    - logs/monitor_v3.log (human-readable console mirror)
"""

import sys
import os
import time
import json
import gzip
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any

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
    # Assets to monitor (MSTR removed - no 15m markets exist)
    coins: List[str] = None

    # Scan intervals (milliseconds)
    aggressive_interval_ms: int = 500   # During start/end of window
    normal_interval_ms: int = 2000      # During middle of window

    # Window phases (seconds)
    aggressive_window_secs: int = 120   # First/last N seconds = aggressive

    # Order book depth
    book_depth: int = 10                # Number of price levels to capture

    # Execution simulation sizes
    sim_sizes: List[int] = None

    # Opportunity threshold
    opportunity_threshold: float = 1.00  # Log when combined_ask < this

    # Output paths
    log_dir: str = "data/logs"
    console_log: str = "logs/monitor_v3.log"

    # Threading
    max_workers: int = 8

    def __post_init__(self):
        if self.coins is None:
            self.coins = ["BTC", "ETH", "SOL", "XRP"]
        if self.sim_sizes is None:
            self.sim_sizes = [50, 100, 200, 500, 1000]


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
    depth_prices: Dict[str, float]  # depth_100, depth_200, etc.

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


# =============================================================================
# LOGGING SETUP
# =============================================================================

def setup_logging(console_log_path: str) -> logging.Logger:
    """Setup dual logging to console and file."""
    logger = logging.getLogger("monitor_v3")
    logger.setLevel(logging.INFO)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%H:%M:%S'))
    logger.addHandler(console)

    # File handler
    Path(console_log_path).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(console_log_path, mode='a')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
    logger.addHandler(file_handler)

    return logger


# =============================================================================
# ORDER BOOK PROCESSING
# =============================================================================

def parse_order_book(raw_book: Any, depth: int) -> OrderBook:
    """Parse raw order book response into structured format."""
    bids = []
    asks = []

    # Parse bids
    if hasattr(raw_book, 'bids') and raw_book.bids:
        for b in raw_book.bids[:depth]:
            bids.append(OrderLevel(
                price=float(b.price),
                size=float(b.size)
            ))

    # Parse asks
    if hasattr(raw_book, 'asks') and raw_book.asks:
        for a in raw_book.asks[:depth]:
            asks.append(OrderLevel(
                price=float(a.price),
                size=float(a.size)
            ))

    # Sort: bids descending, asks ascending
    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)

    best_bid = bids[0].price if bids else 0.0
    best_ask = asks[0].price if asks else 1.0
    spread = best_ask - best_bid

    return OrderBook(
        bids=bids,
        asks=asks,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread
    )


def calculate_depth_price(book: OrderBook, target_size: float, side: str = "ask") -> float:
    """
    Calculate volume-weighted average price to fill target_size.

    Args:
        book: Parsed order book
        target_size: Dollar amount to fill
        side: "ask" (buying) or "bid" (selling)

    Returns:
        VWAP to fill the target size, or 0 if insufficient liquidity
    """
    levels = book.asks if side == "ask" else book.bids

    if not levels:
        return 0.0

    remaining = target_size
    total_cost = 0.0
    total_filled = 0.0

    for level in levels:
        # Size at this level in dollar terms
        level_value = level.size * level.price

        if remaining <= level_value:
            # Can fill remaining at this level
            tokens_needed = remaining / level.price
            total_cost += remaining
            total_filled += tokens_needed
            remaining = 0
            break
        else:
            # Take entire level
            total_cost += level_value
            total_filled += level.size
            remaining -= level_value

    if remaining > 0:
        # Insufficient liquidity
        return 0.0

    # Return average price (cost per token)
    return total_cost / total_filled if total_filled > 0 else 0.0


def simulate_execution(up_book: OrderBook, down_book: OrderBook, size: float) -> Dict:
    """
    Simulate buying both UP and DOWN tokens for arbitrage.

    Args:
        up_book: UP token order book
        down_book: DOWN token order book
        size: Total dollar amount to deploy (split between UP and DOWN)

    Returns:
        Dict with cost, avg_price, and profit
    """
    # For arbitrage, we buy $size worth of the PAIR
    # Each side gets $size/2? No - we need to buy equal TOKEN amounts
    # If UP = 0.40 and DOWN = 0.60, to get 100 tokens of each:
    # Cost = 100 * 0.40 + 100 * 0.60 = 100 * (0.40 + 0.60) = 100 * combined

    # Simpler model: buy $size total, split by current price ratio
    # Actually for arb we want to guarantee $1 payout per token pair
    # So we buy N tokens of UP and N tokens of DOWN
    # Total cost = N * (up_ask + down_ask) = N * combined_ask
    # Payout = N * $1 = N
    # N tokens we can buy = size / combined_ask

    combined_ask = up_book.best_ask + down_book.best_ask

    if combined_ask <= 0:
        return {"cost": size, "avg_price": 0, "profit": -size, "tokens": 0}

    # How many token pairs can we buy?
    tokens = size / combined_ask

    # Calculate actual fill prices with slippage
    up_fill_price = calculate_depth_price(up_book, tokens * up_book.best_ask, "ask")
    down_fill_price = calculate_depth_price(down_book, tokens * down_book.best_ask, "ask")

    if up_fill_price == 0 or down_fill_price == 0:
        # Insufficient liquidity
        return {"cost": size, "avg_price": 0, "profit": -size, "tokens": 0, "error": "insufficient_liquidity"}

    actual_combined = up_fill_price + down_fill_price
    actual_cost = tokens * actual_combined
    payout = tokens  # Each token pair pays $1
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
    """High-performance market scanner with parallel fetching."""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.gamma = GammaClient()
        self.clob = ClobClient(host="https://clob.polymarket.com")
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers)

        # Cache for token IDs (refreshed each 15-min window)
        self.token_cache: Dict[str, Dict] = {}
        self.cache_window_ts: int = 0

    def _get_window_timestamp(self) -> int:
        """Get current 15-minute window start timestamp."""
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window = now.replace(minute=minute, second=0, microsecond=0)
        return int(window.timestamp())

    def _refresh_token_cache(self) -> None:
        """Refresh token ID cache for current window."""
        current_window = self._get_window_timestamp()

        if current_window == self.cache_window_ts and self.token_cache:
            return  # Cache still valid

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
        """Fetch and parse order book for a token."""
        try:
            raw = self.clob.get_order_book(token_id)
            return parse_order_book(raw, self.config.book_depth)
        except Exception as e:
            return None

    def scan_all_markets(self) -> List[MarketSnapshot]:
        """Scan all markets in parallel."""
        self._refresh_token_cache()

        now = datetime.now(timezone.utc)
        results = []

        # Build list of tokens to fetch
        fetch_tasks = []
        for coin, cache in self.token_cache.items():
            if cache.get("up_token") and cache.get("down_token"):
                fetch_tasks.append((coin, "up", cache["up_token"]))
                fetch_tasks.append((coin, "down", cache["down_token"]))

        # Fetch all order books in parallel
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

        # Build snapshots
        for coin in self.config.coins:
            if coin not in books or "up" not in books[coin] or "down" not in books[coin]:
                continue

            cache = self.token_cache.get(coin, {})
            up_book = books[coin]["up"]
            down_book = books[coin]["down"]

            # Calculate remaining time
            end_str = cache.get("end_date", "")
            remaining_secs = 0.0
            if end_str:
                try:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    remaining_secs = (end_dt - now).total_seconds()
                except:
                    pass

            # Build token snapshots
            up_snapshot = TokenSnapshot(
                token_id=books[coin].get("up_token", ""),
                best_bid=up_book.best_bid,
                best_ask=up_book.best_ask,
                spread=up_book.spread,
                book={
                    "bids": [asdict(l) for l in up_book.bids],
                    "asks": [asdict(l) for l in up_book.asks]
                },
                depth_prices={
                    f"depth_{s}": calculate_depth_price(up_book, s, "ask")
                    for s in self.config.sim_sizes
                }
            )

            down_snapshot = TokenSnapshot(
                token_id=books[coin].get("down_token", ""),
                best_bid=down_book.best_bid,
                best_ask=down_book.best_ask,
                spread=down_book.spread,
                book={
                    "bids": [asdict(l) for l in down_book.bids],
                    "asks": [asdict(l) for l in down_book.asks]
                },
                depth_prices={
                    f"depth_{s}": calculate_depth_price(down_book, s, "ask")
                    for s in self.config.sim_sizes
                }
            )

            # Combined metrics
            combined_ask = up_book.best_ask + down_book.best_ask
            combined_bid = up_book.best_bid + down_book.best_bid
            margin = 1.0 - combined_ask

            # Execution simulations
            exec_sim = {}
            for size in self.config.sim_sizes:
                exec_sim[size] = simulate_execution(up_book, down_book, size)

            results.append(MarketSnapshot(
                coin=coin,
                slug=cache.get("slug", ""),
                remaining_seconds=remaining_secs,
                up=up_snapshot,
                down=down_snapshot,
                combined_ask=combined_ask,
                combined_bid=combined_bid,
                margin=margin,
                execution_sim=exec_sim
            ))

        return results

    def shutdown(self):
        """Cleanup resources."""
        self.executor.shutdown(wait=False)


# =============================================================================
# JSONL WRITER
# =============================================================================

class JSONLWriter:
    """Efficient JSONL file writer with rotation."""

    def __init__(self, base_dir: str, prefix: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix
        self.current_date: str = ""
        self.file_handle = None

    def _get_filename(self) -> Path:
        """Get filename for current date."""
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self.base_dir / f"{self.prefix}_{date_str}.jsonl"

    def _ensure_file(self) -> None:
        """Ensure file is open for current date."""
        current_date = datetime.now(timezone.utc).strftime("%Y%m%d")

        if current_date != self.current_date:
            if self.file_handle:
                self.file_handle.close()

            self.current_date = current_date
            filepath = self._get_filename()
            self.file_handle = open(filepath, "a", encoding="utf-8")

    def write(self, data: Dict) -> None:
        """Write a single record."""
        self._ensure_file()
        self.file_handle.write(json.dumps(data, separators=(',', ':')) + "\n")
        self.file_handle.flush()

    def close(self):
        """Close file handle."""
        if self.file_handle:
            self.file_handle.close()


# =============================================================================
# MAIN MONITOR LOOP
# =============================================================================

def get_window_phase(aggressive_window: int) -> tuple:
    """
    Determine current phase within 15-min window.

    Returns:
        (phase, seconds_into_window, seconds_remaining)
    """
    now = datetime.now(timezone.utc)
    seconds_into_window = (now.minute % 15) * 60 + now.second
    seconds_remaining = 900 - seconds_into_window

    if seconds_into_window < aggressive_window:
        return 'start', seconds_into_window, seconds_remaining
    elif seconds_remaining < aggressive_window:
        return 'end', seconds_into_window, seconds_remaining
    else:
        return 'middle', seconds_into_window, seconds_remaining


def snapshot_to_dict(snapshot: MarketSnapshot) -> Dict:
    """Convert MarketSnapshot to JSON-serializable dict."""
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
    """Main monitor loop."""
    logger = setup_logging(config.console_log)
    scanner = MarketScanner(config, logger)
    snapshot_writer = JSONLWriter(config.log_dir, "market_snapshots")
    opportunity_writer = JSONLWriter(config.log_dir, "opportunities")

    logger.info("=" * 60)
    logger.info("  GABAGOOL MONITOR v3 - COMPREHENSIVE ORDER BOOK LOGGER")
    logger.info("=" * 60)
    logger.info(f"Assets: {', '.join(config.coins)}")
    logger.info(f"Aggressive interval: {config.aggressive_interval_ms}ms")
    logger.info(f"Normal interval: {config.normal_interval_ms}ms")
    logger.info(f"Book depth: {config.book_depth} levels")
    logger.info(f"Opportunity threshold: {config.opportunity_threshold}")
    logger.info(f"Output: {config.log_dir}/")
    logger.info("=" * 60)
    logger.info("Press Ctrl+C to stop\n")

    scan_count = 0
    opportunities_found = 0
    last_phase = None

    try:
        while True:
            scan_start = time.time()
            scan_count += 1
            now = datetime.now(timezone.utc)

            phase, secs_in, secs_remain = get_window_phase(config.aggressive_window_secs)

            # Phase transition notification
            if phase != last_phase:
                logger.info(f"\n>>> PHASE: {phase.upper()} <<<\n")
                last_phase = phase

            # Scan markets
            snapshots = scanner.scan_all_markets()
            scan_elapsed = time.time() - scan_start

            # Build full record
            record = {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "ts_epoch_ms": int(now.timestamp() * 1000),
                "scan_id": scan_count,
                "scan_duration_ms": round(scan_elapsed * 1000, 1),
                "window": {
                    "seconds_in": secs_in,
                    "seconds_remain": secs_remain,
                    "phase": phase
                },
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

                # Check for opportunities
                for snap in snapshots:
                    if snap.combined_ask < config.opportunity_threshold:
                        opportunities_found += 1
                        opp_record = {
                            "ts": record["ts"],
                            "ts_epoch_ms": record["ts_epoch_ms"],
                            "scan_id": scan_count,
                            "coin": snap.coin,
                            "combined_ask": round(snap.combined_ask, 4),
                            "margin": round(snap.margin, 4),
                            "up_ask": snap.up.best_ask,
                            "down_ask": snap.down.best_ask,
                            "up_size_at_best": snap.up.book["asks"][0]["size"] if snap.up.book["asks"] else 0,
                            "down_size_at_best": snap.down.book["asks"][0]["size"] if snap.down.book["asks"] else 0,
                            "execution_sim": snap.execution_sim
                        }
                        opportunity_writer.write(opp_record)
                        logger.info(f"🎯 OPPORTUNITY: {snap.coin} Combined={snap.combined_ask:.3f} Margin={snap.margin:.2%}")

            # Write snapshot
            snapshot_writer.write(record)

            # Console output
            if phase in ('start', 'end'):
                # Compact for aggressive mode
                best_str = f"{best.coin}={best.combined_ask:.3f}" if snapshots else "N/A"
                logger.info(f"#{scan_count} Best:{best_str} Scan:{scan_elapsed*1000:.0f}ms Phase:{phase}({secs_in}s/{secs_remain}s)")
            else:
                # Detailed for normal mode
                logger.info(f"\nScan #{scan_count} ({scan_elapsed*1000:.0f}ms)")
                logger.info("-" * 50)
                for snap in snapshots:
                    status = f" (margin: {snap.margin:.1%})" if snap.margin > 0 else ""
                    logger.info(f"  {snap.coin}: UP={snap.up.best_ask:.3f} DOWN={snap.down.best_ask:.3f} "
                               f"Combined={snap.combined_ask:.3f} [{snap.remaining_seconds:.0f}s]{status}")
                logger.info(f"  Window: {secs_in}s in, {secs_remain}s remain | Opportunities: {opportunities_found}")

            # Calculate sleep time
            if phase in ('start', 'end'):
                target_interval = config.aggressive_interval_ms / 1000
            else:
                target_interval = config.normal_interval_ms / 1000

            sleep_time = max(0, target_interval - scan_elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info(f"\n\nStopped after {scan_count} scans")
        logger.info(f"Total opportunities: {opportunities_found}")
        logger.info(f"Data saved to: {config.log_dir}/")

    finally:
        scanner.shutdown()
        snapshot_writer.close()
        opportunity_writer.close()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Gabagool Monitor v3 - Order Book Logger")
    parser.add_argument("--aggressive-interval", type=int, default=500,
                       help="Scan interval in ms during aggressive phase (default: 500)")
    parser.add_argument("--normal-interval", type=int, default=2000,
                       help="Scan interval in ms during normal phase (default: 2000)")
    parser.add_argument("--depth", type=int, default=10,
                       help="Order book depth to capture (default: 10)")
    parser.add_argument("--threshold", type=float, default=1.00,
                       help="Combined ask threshold for opportunities (default: 1.00)")
    parser.add_argument("--log-dir", type=str, default="data/logs",
                       help="Directory for JSONL output (default: data/logs)")

    args = parser.parse_args()

    config = Config(
        aggressive_interval_ms=args.aggressive_interval,
        normal_interval_ms=args.normal_interval,
        book_depth=args.depth,
        opportunity_threshold=args.threshold,
        log_dir=args.log_dir
    )

    run_monitor(config)


if __name__ == "__main__":
    main()
