#!/usr/bin/env python3
"""
Gabagool Bot - Auto Redeemer

Purpose:
    Automatically detect settled markets and redeem positions.
    Runs as background service, polling for resolved markets.

Author: AI-Generated
Created: 2026-01-26
Modified: 2026-01-26

Source:
    Based on: samples/lorine93s-mm/src/services/auto_redeem.py
    Key patterns to extract:
        - Market resolution detection
        - Automated redemption execution
        - Stats integration

Dependencies:
    - asyncio
    - logging

Usage:
    from src.auto_redeem import AutoRedeemer

    redeemer = AutoRedeemer(bot, position_tracker, stats_tracker)

    # Run as background task
    task = asyncio.create_task(redeemer.run_continuous())

    # Or check once
    await redeemer.check_and_redeem()

Notes:
    - Polls every 5 minutes by default
    - Automatically calculates realized profit
    - Updates position tracker and stats tracker
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Tuple, Any


class AutoRedeemer:
    """
    Automated position redemption service.

    Based on lorine93s/polymarket-market-maker-bot auto_redeem.py.
    Detects settled markets and redeems winning positions.
    """

    def __init__(
        self,
        bot: Any,
        position_tracker: Any,
        stats_tracker: Any,
        check_interval: int = 300  # 5 minutes
    ):
        """
        Initialize auto-redeemer.

        Args:
            bot: TradingBot instance for API calls
            position_tracker: PositionTracker instance
            stats_tracker: StatsTracker instance
            check_interval: Seconds between checks (default 300)
        """
        self.bot = bot
        self.position_tracker = position_tracker
        self.stats_tracker = stats_tracker
        self.check_interval = check_interval
        self.logger = logging.getLogger("auto_redeemer")
        self.running = False

    async def check_and_redeem(self) -> int:
        """
        Check all active positions for settlements.
        Redeem positions and calculate realized profit.

        Returns:
            Number of positions redeemed
        """
        redeemed_count = 0
        complete_pairs = self.position_tracker.get_complete_pairs()

        for position in complete_pairs:
            if position.resolved:
                continue  # Already processed

            # Check if market is resolved
            is_resolved, winning_side = await self.check_market_resolved(
                position.market_id
            )

            if is_resolved:
                profit = await self.redeem_position(position, winning_side)
                if profit is not None:
                    redeemed_count += 1

        if redeemed_count > 0:
            self.logger.info("Redeemed %d positions", redeemed_count)

        return redeemed_count

    async def check_market_resolved(
        self,
        market_id: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Query Polymarket API to check if market is resolved.

        Args:
            market_id: Market identifier

        Returns:
            Tuple of (is_resolved, winning_side)
            winning_side is 'YES' or 'NO' or None if not resolved
        """
        try:
            market_info = await self.bot.get_market_info(market_id)

            if not market_info:
                return False, None

            is_closed = market_info.get("closed", False)
            is_resolved = market_info.get("resolved", False)

            if is_closed and is_resolved:
                # Determine winning outcome
                # This depends on how Polymarket reports resolution
                winning_outcome = market_info.get("winning_outcome")

                if winning_outcome is not None:
                    # Convert to YES/NO
                    winning_side = "YES" if winning_outcome else "NO"
                    self.logger.info(
                        "Market %s resolved: %s wins",
                        market_id[:16], winning_side
                    )
                    return True, winning_side

            return False, None

        except Exception as e:
            self.logger.error("Error checking market resolution: %s", e)
            return False, None

    async def redeem_position(
        self,
        position: Any,
        winning_side: str
    ) -> Optional[float]:
        """
        Redeem settled position and calculate profit.

        Args:
            position: ArbitragePosition object
            winning_side: 'YES' or 'NO'

        Returns:
            Realized profit in USD, or None if failed
        """
        try:
            self.logger.info(
                "Redeeming position: %s | Winner: %s",
                position.market_id[:16], winning_side
            )

            # For gabagool arbitrage, we always profit (both sides held)
            # Profit = 1.0 - combined_avg_cost per pair
            pairs = position.total_pairs
            profit_per_pair = position.guaranteed_profit_per_pair
            total_profit = profit_per_pair * pairs

            # Execute redemption on Polymarket
            success = await self.bot.redeem_position(
                position.yes_token_id,
                position.no_token_id,
                pairs
            )

            if success:
                # Mark position as resolved
                self.position_tracker.mark_resolved(
                    position.market_id,
                    profit=total_profit
                )

                # Update stats tracker
                self.stats_tracker.update_trade_result(
                    position.market_id,
                    "success",
                    total_profit
                )

                self.logger.info(
                    "Position redeemed: %s | Pairs: %.2f | Profit: $%.4f",
                    position.market_id[:16], pairs, total_profit
                )

                return total_profit
            else:
                self.logger.error(
                    "Redemption failed for %s", position.market_id[:16]
                )
                return None

        except Exception as e:
            self.logger.error("Redemption error: %s", e)
            self.stats_tracker.update_trade_result(
                position.market_id,
                "failed",
                0.0,
                notes=str(e)
            )
            return None

    async def run_continuous(self) -> None:
        """
        Run auto-redemption service continuously.
        Runs as background task, checking periodically.
        """
        self.running = True
        self.logger.info(
            "Auto-redemption service started (interval: %ds)",
            self.check_interval
        )

        while self.running:
            try:
                await self.check_and_redeem()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                self.logger.info("Auto-redemption service cancelled")
                break
            except Exception as e:
                self.logger.error("Auto-redemption error: %s", e)
                await asyncio.sleep(60)  # Wait 1 min on error

        self.running = False
        self.logger.info("Auto-redemption service stopped")

    def stop(self) -> None:
        """Stop the continuous redemption service."""
        self.running = False
        self.logger.info("Auto-redemption service stopping...")
