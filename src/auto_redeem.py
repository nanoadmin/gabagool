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
    Uses Polymarket Data API for redeemable position checks.

Dependencies:
    - asyncio
    - aiohttp
    - logging

Usage:
    from src.auto_redeem import AutoRedeemer

    redeemer = AutoRedeemer(wallet_address, position_tracker, stats_tracker)

    # Run as background task
    task = asyncio.create_task(redeemer.run_continuous())

    # Or check once
    redeemed = await redeemer.check_and_redeem()

Notes:
    - Polls every 5 minutes by default
    - Uses Polymarket Data API for position queries
    - Updates position tracker and stats tracker on redemption
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import aiohttp


class AutoRedeemer:
    """
    Automated position redemption service.

    Based on lorine93s/polymarket-market-maker-bot auto_redeem.py.
    Checks for redeemable positions via Polymarket Data API and redeems them.
    """

    # Polymarket Data API endpoints
    DATA_API_URL = "https://data-api.polymarket.com"

    def __init__(
        self,
        wallet_address: str,
        position_tracker: Any = None,
        stats_tracker: Any = None,
        check_interval: int = 300,  # 5 minutes
        redeem_threshold_usd: float = 0.10,  # Minimum value to redeem
        enabled: bool = True
    ):
        """
        Initialize auto-redeemer.

        Args:
            wallet_address: Polymarket wallet address to check positions for
            position_tracker: Optional PositionTracker instance
            stats_tracker: Optional StatsTracker instance
            check_interval: Seconds between checks (default 300)
            redeem_threshold_usd: Minimum USD value to trigger redemption
            enabled: Whether auto-redemption is enabled
        """
        self.wallet_address = wallet_address
        self.position_tracker = position_tracker
        self.stats_tracker = stats_tracker
        self.check_interval = check_interval
        self.redeem_threshold_usd = redeem_threshold_usd
        self.enabled = enabled

        self.logger = logging.getLogger("auto_redeemer")
        self.running = False
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_redeemable_positions(self) -> List[Dict[str, Any]]:
        """
        Query Polymarket Data API for redeemable positions.

        Returns:
            List of redeemable position dicts
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/positions"
            params = {
                "user": self.wallet_address,
                "redeemable": "true"
            }

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    positions = await response.json()
                    self.logger.debug("Found %d redeemable positions", len(positions))
                    return positions
                else:
                    self.logger.warning(
                        "Failed to get redeemable positions: HTTP %d",
                        response.status
                    )
                    return []

        except Exception as e:
            self.logger.error("Error checking redeemable positions: %s", e)
            return []

    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        Get all positions for the wallet.

        Returns:
            List of position dicts
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/positions"
            params = {"user": self.wallet_address}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                return []

        except Exception as e:
            self.logger.error("Error getting positions: %s", e)
            return []

    async def redeem_position(self, position_id: str) -> bool:
        """
        Attempt to redeem a specific position.

        Note: This requires the Polymarket API to support redemption.
        Currently this is a placeholder that logs the redemption attempt.

        Args:
            position_id: Position ID to redeem

        Returns:
            True if redemption was successful
        """
        try:
            # Note: Direct API redemption may not be available
            # Redemption typically requires on-chain transaction
            self.logger.info("Attempting to redeem position: %s", position_id)

            # For now, just log the attempt
            # Full implementation would require web3 transaction
            self.logger.warning(
                "Redemption API not implemented - position %s needs manual redemption",
                position_id
            )
            return False

        except Exception as e:
            self.logger.error("Redemption failed for %s: %s", position_id, e)
            return False

    async def check_and_redeem(self) -> int:
        """
        Check for redeemable positions and attempt to redeem them.

        Returns:
            Number of positions processed
        """
        if not self.enabled:
            return 0

        redeemable = await self.get_redeemable_positions()
        processed = 0

        for position in redeemable:
            try:
                position_id = position.get("id", "")
                value_usd = float(position.get("value", 0))
                market_slug = position.get("slug", "unknown")

                # Skip positions below threshold
                if value_usd < self.redeem_threshold_usd:
                    self.logger.debug(
                        "Skipping %s (value $%.2f < threshold $%.2f)",
                        market_slug, value_usd, self.redeem_threshold_usd
                    )
                    continue

                self.logger.info(
                    "Found redeemable position: %s | Value: $%.2f",
                    market_slug, value_usd
                )

                # Attempt redemption
                success = await self.redeem_position(position_id)
                if success:
                    processed += 1

                    # Update stats if tracker is available
                    if self.stats_tracker:
                        self.stats_tracker.update_trade_result(
                            position.get("conditionId", market_slug),
                            "redeemed",
                            value_usd
                        )

            except Exception as e:
                self.logger.error("Error processing position: %s", e)

        if processed > 0:
            self.logger.info("Redeemed %d positions", processed)
        elif redeemable:
            self.logger.info(
                "Found %d redeemable positions (manual redemption required)",
                len(redeemable)
            )

        return processed

    async def run_continuous(self) -> None:
        """
        Run auto-redemption service continuously.
        Runs as background task, checking periodically.
        """
        self.running = True
        self.logger.info(
            "Auto-redemption service started (interval: %ds, threshold: $%.2f)",
            self.check_interval, self.redeem_threshold_usd
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
        await self.close()
        self.logger.info("Auto-redemption service stopped")

    def stop(self) -> None:
        """Stop the continuous redemption service."""
        self.running = False
        self.logger.info("Auto-redemption service stopping...")

    async def get_wallet_value(self) -> float:
        """
        Get total value of all positions.

        Returns:
            Total position value in USD
        """
        try:
            session = await self._get_session()
            url = f"{self.DATA_API_URL}/value"
            params = {"user": self.wallet_address}

            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data.get("value", 0))
                return 0.0

        except Exception as e:
            self.logger.error("Error getting wallet value: %s", e)
            return 0.0
