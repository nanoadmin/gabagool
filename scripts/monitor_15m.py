#!/usr/bin/env python3
"""
Gabagool 15-Minute Market Monitor (Aggressive Mode)

Purpose:
    Monitor 15-minute markets with aggressive scanning during
    window transitions (first/last 1 minute of each 15-min period).

Author: AI-Generated
Created: 2026-01-27
Modified: 2026-01-27

Usage:
    python scripts/monitor_15m.py
"""

import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gamma_client import GammaClient
from py_clob_client.client import ClobClient

# Config
COINS = ["BTC", "ETH", "SOL", "XRP", "MSTR"]  # MSTR = MicroStrategy (tracks BTC)
YES_THRESHOLD = 0.48
NO_THRESHOLD = 0.48
MAX_COMBINED = 0.97

# Scan intervals
AGGRESSIVE_INTERVAL = 1.0   # 1 second during critical windows
NORMAL_INTERVAL = 10.0      # 10 seconds during normal times
AGGRESSIVE_WINDOW = 60      # First/last 60 seconds of each 15-min period

gamma = GammaClient()
clob = ClobClient(host="https://clob.polymarket.com")


def get_window_phase():
    """
    Determine current phase within 15-min window.

    Returns:
        (phase, seconds_into_window, seconds_remaining)
        phase: 'start', 'middle', or 'end'
    """
    now = datetime.now(timezone.utc)
    seconds_into_window = (now.minute % 15) * 60 + now.second
    seconds_remaining = 900 - seconds_into_window

    if seconds_into_window < AGGRESSIVE_WINDOW:
        return 'start', seconds_into_window, seconds_remaining
    elif seconds_remaining < AGGRESSIVE_WINDOW:
        return 'end', seconds_into_window, seconds_remaining
    else:
        return 'middle', seconds_into_window, seconds_remaining


def scan_markets():
    """Scan all 15-min markets and return opportunities."""
    now = datetime.now(timezone.utc)
    results = []

    for coin in COINS:
        try:
            market = gamma.get_current_15m_market(coin)
            if not market:
                continue

            slug = market.get("slug", "")
            end_str = market.get("endDate", "")

            if end_str:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                remaining = (end_dt - now).total_seconds() / 60
            else:
                remaining = 0

            token_ids = gamma.parse_token_ids(market)
            up_token = token_ids.get("up") or token_ids.get("yes")
            down_token = token_ids.get("down") or token_ids.get("no")

            if not up_token or not down_token:
                continue

            up_book = clob.get_order_book(up_token)
            down_book = clob.get_order_book(down_token)

            up_asks = up_book.asks if hasattr(up_book, 'asks') else []
            down_asks = down_book.asks if hasattr(down_book, 'asks') else []

            if not up_asks or not down_asks:
                continue

            best_up_ask = min(float(a.price) for a in up_asks)
            best_down_ask = min(float(a.price) for a in down_asks)

            up_size = sum(float(a.size) for a in up_asks if float(a.price) == best_up_ask)
            down_size = sum(float(a.size) for a in down_asks if float(a.price) == best_down_ask)

            combined = best_up_ask + best_down_ask
            margin = 1.0 - combined

            results.append({
                'coin': coin,
                'slug': slug,
                'remaining': remaining,
                'up_ask': best_up_ask,
                'down_ask': best_down_ask,
                'up_size': up_size,
                'down_size': down_size,
                'combined': combined,
                'margin': margin,
                'up_token': up_token,
                'down_token': down_token,
                'is_opportunity': (
                    best_up_ask < YES_THRESHOLD and
                    best_down_ask < NO_THRESHOLD and
                    combined < MAX_COMBINED
                )
            })

        except Exception as e:
            pass  # Silent fail for speed

    return results


def main():
    print("="*60)
    print("  GABAGOOL 15-MINUTE MARKET MONITOR (AGGRESSIVE)")
    print("="*60)
    print(f"Assets: {', '.join(COINS)}")
    print(f"Thresholds: YES<{YES_THRESHOLD}, NO<{NO_THRESHOLD}, Combined<{MAX_COMBINED}")
    print(f"Aggressive scan: {AGGRESSIVE_INTERVAL}s during first/last {AGGRESSIVE_WINDOW}s")
    print(f"Normal scan: {NORMAL_INTERVAL}s during middle period")
    print("="*60)
    print("Press Ctrl+C to stop\n")

    scan_count = 0
    opportunities_found = 0
    last_phase = None

    try:
        while True:
            scan_count += 1
            now = datetime.now(timezone.utc)
            phase, secs_in, secs_remain = get_window_phase()

            # Determine scan interval
            if phase in ('start', 'end'):
                interval = AGGRESSIVE_INTERVAL
                mode = f"AGGRESSIVE ({phase})"
            else:
                interval = NORMAL_INTERVAL
                mode = "normal"

            # Phase transition notification
            if phase != last_phase:
                print(f"\n>>> PHASE: {phase.upper()} - Switching to {mode} mode <<<\n")
                last_phase = phase

            # Compact output for aggressive mode, detailed for normal
            if phase in ('start', 'end'):
                # Single line per scan for speed
                results = scan_markets()
                opps = [r for r in results if r['is_opportunity']]

                if opps:
                    opportunities_found += len(opps)
                    for o in opps:
                        print(f"[{now.strftime('%H:%M:%S')}] 🎯 {o['coin']}: "
                              f"UP={o['up_ask']:.3f} DOWN={o['down_ask']:.3f} "
                              f"Combined={o['combined']:.3f} MARGIN={o['margin']:.2%}")
                else:
                    # Compact status line
                    best = min(results, key=lambda x: x['combined']) if results else None
                    if best:
                        print(f"[{now.strftime('%H:%M:%S')}] #{scan_count} "
                              f"Best: {best['coin']}={best['combined']:.3f} "
                              f"Phase: {phase} ({secs_in}s in, {secs_remain}s left)")
            else:
                # Detailed output for normal mode
                print(f"\n[{now.strftime('%H:%M:%S')}] Scan #{scan_count} ({mode})")
                print("-"*50)

                results = scan_markets()

                for r in results:
                    status = ""
                    if r['is_opportunity']:
                        status = " 🎯 OPPORTUNITY!"
                        opportunities_found += 1
                    elif r['combined'] < 1.0:
                        status = f" (margin: {r['margin']:.1%})"

                    print(f"  {r['coin']:4}: UP={r['up_ask']:.3f} DOWN={r['down_ask']:.3f} "
                          f"Combined={r['combined']:.3f} [{r['remaining']:.1f}m]{status}")

                    if r['is_opportunity']:
                        print(f"       >>> BUY UP @ ${r['up_ask']:.3f} + DOWN @ ${r['down_ask']:.3f}")
                        print(f"       >>> Size: UP={r['up_size']:.1f}, DOWN={r['down_size']:.1f}")

                print(f"\n  Window: {secs_in}s in, {secs_remain}s remain | "
                      f"Total opportunities: {opportunities_found}")

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n\nStopped after {scan_count} scans")
        print(f"Total opportunities found: {opportunities_found}")


if __name__ == "__main__":
    main()
