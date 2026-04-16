from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from src.paper_service import PaperTradingService, MarketSnapshot, utc_now


def make_service(tmp_path):
    service = PaperTradingService(
        state_path=Path(tmp_path) / "state.json",
        test_report_path=Path(tmp_path) / "tests.json",
    )
    service.starting_balance = 500
    service.max_trade_stake = 100
    service.state = service._default_state()
    service.state["starting_balance"] = 500
    service.state["cash_balance"] = 500
    return service


def test_opportunity_opens_position(tmp_path):
    service = make_service(tmp_path)
    snapshot = MarketSnapshot(
        asset="BTC",
        slug="demo-btc",
        question="BTC higher?",
        yes_price=0.46,
        no_price=0.47,
        combined_cost=0.93,
        profit_margin=0.07,
        end_date=(utc_now() + timedelta(minutes=15)).isoformat(),
        volume=1000,
        accepting_orders=True,
        data_source="demo",
        opportunity=True,
    )

    service._open_new_positions_locked([snapshot])

    assert len(service.state["open_positions"]) == 1
    assert service.state["cash_balance"] < 500


def test_settlement_realizes_profit(tmp_path):
    service = make_service(tmp_path)
    service.state["open_positions"] = [
        {
            "asset": "BTC",
            "slug": "demo-btc",
            "question": "BTC higher?",
            "yes_price": 0.46,
            "no_price": 0.47,
            "combined_cost": 0.93,
            "profit_margin": 0.07,
            "shares": 100,
            "cost": 93,
            "payout": 100,
            "expected_profit": 7,
            "profit": 7,
            "opened_at": utc_now().isoformat(),
            "resolve_at": (utc_now() - timedelta(minutes=2)).isoformat(),
            "status": "open",
            "data_source": "demo",
        }
    ]
    service.state["cash_balance"] = 407

    service._settle_due_positions_locked()

    assert not service.state["open_positions"]
    assert service.state["closed_positions"][0]["profit"] == 7
    assert service.state["cash_balance"] == 507


def test_reset_bankroll_clears_positions(tmp_path):
    service = make_service(tmp_path)
    service.state["open_positions"] = [{"slug": "demo"}]
    service.state["closed_positions"] = [{"slug": "closed"}]
    service.state["cash_balance"] = 120

    snapshot = asyncio.run(service.reset_bankroll())

    assert snapshot["open_positions"] == []
    assert snapshot["closed_positions"] == []
    assert snapshot["summary"]["cash_balance"] == 500
