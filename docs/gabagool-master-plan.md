# Gabagool Bot: Master Plan

*Version 2.0 - Refined with Repository Assessment*
*Last Updated: January 26, 2026*

---

## Executive Summary

**Gabagool** is a Polymarket arbitrage bot that guarantees profit by buying both YES and NO shares when each side becomes temporarily cheap. If combined cost < $1.00, profit is locked regardless of outcome.

**Implementation Approach**: Hybrid build combining best components from 4 proven repositories.

| Metric | Target |
|--------|--------|
| Time to Production | 2.5 days (21 hours) |
| Initial Capital | $100 |
| Target Scale | $15-25k |
| Annual Income Target | $140k+ |
| Win Rate | >95% on paired trades |

---

## Strategy Overview

### Core Concept

```
Buy YES when cheap → Buy NO when cheap → Pair cost < $1.00 → Guaranteed profit
```

**Example:**
- Buy YES @ $0.48 avg
- Buy NO @ $0.45 avg  
- Pair cost: $0.93
- Payout: $1.00
- Profit: $0.07 (7.5% per pair)

### Target Markets

- 15-minute BTC up/down markets
- 15-minute ETH up/down markets
- 15-minute SOL up/down markets
- ~96 markets per day (BTC alone)

### Why It Works

The gabagool strategy exploits temporary mispricings in binary outcome markets. Unlike directional trading:
- No prediction required
- Profit is mathematical, not probabilistic
- Edge is structural, not speed-dependent

---

## Repository Strategy

### Hybrid Approach (Selected)

**Base Repository**: discountry/polymarket-trading-bot (4.76/5.0)

**Component Extraction From**:

| Repository | Score | Extract | Benefit | Time |
|------------|-------|---------|---------|------|
| Trust412/Polymarket-spike-bot-v1 | 4.53 | Position tracker, threading | Concurrent positions, time limits | +3h |
| warproxxx/poly-maker | 4.72 | poly_merger, stats_tracker | 30-50% gas savings, metrics | +3h |
| lorine93s/polymarket-market-maker-bot | 4.85 | risk_manager, auto_redeem | Pre-trade validation, auto settlement | +4h |

**Total Time**: 11h (base) + 10h (extracts) = **21 hours**

### Why Hybrid > Simple Clone

| Capability | Simple Clone | Hybrid |
|------------|--------------|--------|
| Position tracking | Basic dict | Thread-safe + time limits |
| Risk management | Config thresholds | Pre-trade validation framework |
| Gas costs | Full market fees | 30-50% savings |
| Settlement | Manual | Fully automated |
| Concurrent trades | Not optimized | 3+ simultaneous |
| Scaling ceiling | ~$2k | $25k+ |

**The extra 10 hours pays for itself within the first month.**

---

## Project Structure

```
Bots/
└── gabagool/
    ├── config/
    │   ├── default.yaml
    │   ├── production.yaml
    │   └── .env.example
    ├── src/
    │   ├── __init__.py
    │   ├── main.py                 # Entry point & main loop
    │   ├── bot.py                  # Core trading bot (from discountry)
    │   ├── client.py               # Polymarket API client (from discountry)
    │   ├── position_tracker.py     # Thread-safe tracking (from Trust412)
    │   ├── risk_manager.py         # Pre-trade validation (from lorine93s)
    │   ├── poly_merger.py          # Gas optimization (from warproxxx)
    │   ├── stats_tracker.py        # Performance metrics (from warproxxx)
    │   ├── auto_redeem.py          # Settlement automation (from lorine93s)
    │   ├── gamma_client.py         # Market discovery (from discountry)
    │   ├── websocket_client.py     # Real-time prices (from discountry)
    │   └── db.py                   # SQLite persistence
    ├── strategies/
    │   └── gabagool_strategy.py    # Core arbitrage logic (custom)
    ├── tests/
    │   ├── unit/
    │   │   ├── test_position_tracker.py
    │   │   ├── test_risk_manager.py
    │   │   └── test_strategy.py
    │   └── live/
    │       ├── test_api_connection.py
    │       ├── test_wallet_balance.py
    │       └── test_order_placement.py
    ├── backtest/
    │   ├── paper_trade.py
    │   ├── historical_data.py
    │   └── simulate.py
    ├── research/
    │   ├── notebooks/
    │   └── analysis/
    ├── docs/
    │   ├── ARCHITECTURE.md
    │   ├── SETUP.md
    │   ├── PARAMETERS.md
    │   └── RUNBOOK.md
    ├── scripts/
    │   ├── setup_wallet.py
    │   └── deploy.sh
    ├── logs/
    ├── requirements.txt
    └── README.md
```

---

## Component Specifications

### 1. Position Tracker (from Trust412)

**Purpose**: Thread-safe tracking of YES/NO positions with time limits

**Key Features**:
- Lock-based thread safety for concurrent access
- Weighted average cost calculation
- Holding time limits (30 min max)
- Concurrent position limits (max 3)
- Incomplete pair detection
- Expired position cleanup

**Data Model**:
```python
@dataclass
class ArbitragePosition:
    market_id: str
    yes_shares: float
    yes_avg_cost: float
    no_shares: float
    no_avg_cost: float
    opened_at: datetime
    holding_time_limit: int = 1800  # 30 minutes
    
    @property
    def combined_avg_cost(self) -> float
    
    @property
    def is_complete_pair(self) -> bool
    
    @property
    def guaranteed_profit_per_pair(self) -> float
    
    @property
    def is_expired(self) -> bool
```

### 2. Risk Manager (from lorine93s)

**Purpose**: Pre-trade validation to prevent losses

**Validations**:
1. Position count limit (max concurrent)
2. Total exposure limit
3. Profit margin minimum (2%+)
4. Max combined cost ($0.98)
5. Price sanity checks
6. Per-market position limit
7. Liquidity requirements

**Config**:
```python
@dataclass
class RiskConfig:
    max_position_per_market: float = 100.0
    max_total_exposure: float = 500.0
    max_concurrent_arbitrages: int = 3
    min_profit_margin: float = 0.02
    max_combined_cost: float = 0.98
    max_slippage: float = 0.03
    max_position_age_minutes: int = 30
    min_liquidity_per_side: float = 100.0
```

### 3. Gas Optimizer (from warproxxx)

**Purpose**: Reduce transaction costs by 30-50%

**Method**: Position merging via CTF Exchange contract
- Instead of selling YES and NO separately (2 transactions)
- Merge YES + NO → USDC (1 transaction)
- Savings: ~45% gas reduction

### 4. Stats Tracker (from warproxxx)

**Purpose**: Performance metrics and analytics

**Metrics Tracked**:
- Total trades
- Win rate
- Average profit per trade
- Total profit
- Average margin achieved
- Trade history (JSON persistence)

### 5. Auto Redeemer (from lorine93s)

**Purpose**: Automatically settle resolved positions

**Operation**:
- Polls every 5 minutes for resolved markets
- Detects winning outcome
- Executes redemption
- Calculates realized profit
- Updates stats tracker

---

## Parameters

### Entry Thresholds

| Parameter | Default | Range | Notes |
|-----------|---------|-------|-------|
| `yes_buy_threshold` | 0.48 | 0.40-0.52 | Buy YES if price below |
| `no_buy_threshold` | 0.48 | 0.40-0.52 | Buy NO if price below |
| `max_combined_cost` | 0.97 | 0.95-0.98 | Max total for both sides |
| `min_profit_margin` | 0.02 | 0.02-0.05 | Minimum profit to enter |

### Position Limits

| Parameter | $100 Capital | $1k Capital | $15k Capital |
|-----------|--------------|-------------|--------------|
| `max_position_per_market` | $50 | $200 | $1,000 |
| `max_total_exposure` | $100 | $700 | $10,000 |
| `max_concurrent_arbitrages` | 2 | 3 | 5 |
| `max_unpaired_exposure` | $10 | $100 | $1,500 |

### Time Limits

| Parameter | Value | Notes |
|-----------|-------|-------|
| `holding_time_limit` | 1800s (30 min) | Max time to complete pair |
| `min_time_to_resolution` | 120s (2 min) | Don't enter near expiry |
| `redeem_check_interval` | 300s (5 min) | Settlement polling |

---

## Risk Model

### Max Loss Rule

**Constraint**: Max 10% loss per trade

```python
# At any capital level:
MAX_UNPAIRED_EXPOSURE = CAPITAL * 0.10

# Examples:
# $100 capital → $10 max unpaired
# $1,000 capital → $100 max unpaired
# $15,000 capital → $1,500 max unpaired
```

### Circuit Breakers

| Trigger | Action |
|---------|--------|
| 3 consecutive failed transactions | Pause trading, alert |
| Wallet balance < minimum | Stop new positions |
| Drawdown > 15% daily | Stop trading for day |
| API errors > rate limit | Exponential backoff |

---

## Implementation Roadmap

### Day 1 (8 hours) - Foundation

**Morning (4h)**:
1. Clone all 4 repositories
2. Set up project structure
3. Create Python environment
4. Configure .env with test wallet
5. Verify base infrastructure works
6. Test API connection and wallet

**Afternoon (4h)**:
7. Create gabagool_strategy.py skeleton
8. Extract position_tracker.py from Trust412
9. Implement threading patterns
10. Add time limit logic

**End State**: Infrastructure verified, strategy skeleton, position tracker working

### Day 2 (8 hours) - Core Logic

**Morning (4h)**:
11. Implement arbitrage execution logic
12. Extract risk_manager.py from lorine93s
13. Integrate pre-trade validations
14. Test with paper orders

**Afternoon (4h)**:
15. Extract poly_merger.py from warproxxx
16. Extract stats_tracker.py from warproxxx
17. Add JSON persistence
18. Add performance logging

**End State**: Full execution pipeline, risk management, stats tracking

### Day 3 (5 hours) - Deploy

**Morning (3h)**:
19. Extract auto_redeem.py from lorine93s
20. Add SQLite persistence
21. Integration testing with $10

**Afternoon (2h)**:
22. Deploy to Vultr VPS
23. Configure production .env
24. Fund wallet with $100
25. Start live trading

**End State**: Production bot running with $100

### Week 1-2 - Tune

26. Monitor actual pair costs achieved
27. Adjust thresholds based on fill rates
28. Optimize order sizing for slippage
29. Build paper trading mode (parallel)

### Week 3+ - Scale

30. If metrics good, add $1,000
31. Increase position limits
32. Monitor gas optimization impact
33. Continue tuning

---

## Financial Projections

### Per-Trade Economics

| Metric | Value |
|--------|-------|
| Gross margin | 3-4% |
| Gas + slippage | ~1-1.5% |
| Net margin | ~1.5-2.5% |
| Trades per day (at scale) | 20-40 |

### Scaling Path

| Capital | Daily Net | Monthly | Annual |
|---------|-----------|---------|--------|
| $100 | $2-5 | $60-150 | - |
| $1,000 | $20-50 | $600-1,500 | $7-18k |
| $5,000 | $100-250 | $3-7.5k | $36-90k |
| $15,000 | $300-600 | $9-18k | $110-220k |
| $25,000 | $500-900 | $15-27k | $180-330k |

**Target $140k/year → Need ~$15k capital**

---

## Success Metrics

### Launch Criteria (Day 3)

- [ ] API connection stable
- [ ] Wallet funded with $100 USDC + MATIC for gas
- [ ] First test trade executed successfully
- [ ] Position tracking working
- [ ] Risk manager blocking invalid trades
- [ ] Logs writing correctly

### Week 1 Targets

- [ ] 30+ trades completed
- [ ] Win rate > 70% on paired trades
- [ ] Average pair cost < $0.97
- [ ] No manual intervention required
- [ ] Stats tracking accurate

### Month 1 Targets

- [ ] 200+ trades completed
- [ ] Win rate > 85%
- [ ] Total profit > $50 on $100 capital
- [ ] Ready to scale to $1,000

---

## Dependencies

```
# requirements.txt
web3>=6.11.0
py-clob-client>=0.1.0
aiohttp>=3.9.0
sqlalchemy>=2.0.0
python-dotenv>=1.0.0
pyyaml>=6.0
colorlog>=6.7.0
pydantic>=2.0.0
```

---

## VPS & Infrastructure

### Vultr VPS (Existing)

- Verify Netherlands or EU location (Polymarket access)
- Python 3.9+ installed
- Screen or PM2 for process management

### Wallet Setup

1. Generate new Polygon wallet (dedicated to this bot)
2. Fund with MATIC (~$5-10 for gas)
3. Fund with USDC ($100 initial)
4. Approve USDC spending for Polymarket contracts
5. Store private key in .env (never commit)

---

## Comparison: Gabagool vs Gridbot

| Factor | Gridbot | Gabagool |
|--------|---------|----------|
| Research intensity | High | Low |
| Parameter space | Large | Small |
| Backtesting value | High | Medium |
| Profit mechanism | Market prediction | Structural arbitrage |
| Local compute needed | Heavy | Light |
| Time to production | Weeks | Days |

**Gabagool requires much less research** because:
1. Profit is mathematical, not predictive
2. Parameters are mostly independent
3. Fast feedback loop (15-min markets)
4. Failure modes are obvious

**Recommendation**: Go live immediately with $100, tune from real data.

---

## Quick Start Commands

```bash
# 1. Clone base repository (discountry - highest scored skeleton)
git clone https://github.com/discountry/polymarket-trading-bot.git Bots/gabagool
cd Bots/gabagool

# 2. Clone component sources (for extraction only)
mkdir -p ../component-sources
git clone https://github.com/Trust412/Polymarket-spike-bot-v1.git ../component-sources/trust412-spike
git clone https://github.com/warproxxx/poly-maker.git ../component-sources/warproxxx-maker
git clone https://github.com/lorine93s/polymarket-market-maker-bot.git ../component-sources/lorine93s-mm

# 3. Setup environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env with wallet keys

# 5. Test base infrastructure
python examples/quickstart.py

# 6. Extract components (Day 1-2 work)
# - Copy position tracker patterns from trust412-spike
# - Copy risk_manager.py from lorine93s-mm
# - Copy poly_merger.py and stats_tracker.py from warproxxx-maker
# - Copy auto_redeem.py from lorine93s-mm

# 7. Deploy (after Day 3)
scp -r Bots/gabagool/ user@vultr-vps:/home/user/Bots/
ssh user@vultr-vps
cd /home/user/Bots/gabagool
screen -S gabagool
python src/main.py
# Ctrl+A, D to detach
```

---

## Appendix: Repository Sources

| Repository | URL | Stars | Use |
|------------|-----|-------|-----|
| discountry/polymarket-trading-bot | github.com/discountry/polymarket-trading-bot | New | Base |
| Trust412/Polymarket-spike-bot-v1 | github.com/Trust412/Polymarket-spike-bot-v1 | 283 | Position tracker |
| warproxxx/poly-maker | github.com/warproxxx/poly-maker | - | Gas + stats |
| lorine93s/polymarket-market-maker-bot | github.com/lorine93s/polymarket-market-maker-bot | - | Risk + redeem |

---

*End of Master Plan*
