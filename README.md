# Gabagool Bot

**Polymarket Arbitrage Trading Bot**

*Version 1.0 - Hybrid Build*
*Created: 2026-01-26*

---

## Overview

Gabagool is a Polymarket arbitrage bot that guarantees profit by buying both YES and NO shares when each side becomes temporarily cheap. If combined cost < $1.00, profit is locked regardless of outcome.

### Core Concept

```
Buy YES when cheap -> Buy NO when cheap -> Pair cost < $1.00 -> Guaranteed profit
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

---

## Architecture

This bot uses a **hybrid approach**, combining the best components from 4 proven repositories:

| Component | Source | Purpose |
|-----------|--------|---------|
| Base Infrastructure | discountry/polymarket-trading-bot | API client, wallet, order execution |
| Position Tracker | Trust412/Polymarket-spike-bot-v1 | Thread-safe tracking, time limits |
| Gas Optimizer | warproxxx/poly-maker | Position merging, stats tracking |
| Risk Manager | lorine93s/polymarket-market-maker-bot | Pre-trade validation, auto-redeem |

---

## Project Structure

```
gabagool/
├── config/           # Configuration files
├── src/              # Core source code
├── strategies/       # Trading strategies
├── tests/            # Unit and live tests
├── backtest/         # Paper trading and simulation
├── research/         # Analysis notebooks
├── docs/             # Documentation
├── scripts/          # Utility scripts
├── logs/             # Runtime logs
├── samples/          # Reference repositories
└── requirements.txt  # Dependencies
```

---

## Quick Start

```bash
# 1. Setup environment
cd gabagool
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp config/.env.example config/.env
# Edit config/.env with your wallet keys

# 3. Test connection
python -m src.main --dry-run

# 4. Run live
python -m src.main
```

---

## Configuration

See [docs/PARAMETERS.md](docs/PARAMETERS.md) for full parameter documentation.

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `yes_threshold` | 0.48 | Buy YES if price below |
| `no_threshold` | 0.48 | Buy NO if price below |
| `max_combined_cost` | 0.97 | Max total for both sides |
| `min_profit_margin` | 0.02 | Minimum profit to enter |
| `max_concurrent_arbitrages` | 3 | Max simultaneous positions |

---

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design
- [SETUP.md](docs/SETUP.md) - Installation guide
- [PARAMETERS.md](docs/PARAMETERS.md) - Configuration reference
- [RUNBOOK.md](docs/RUNBOOK.md) - Operations guide

---

## Safety

- Never delete data or code
- Use dedicated trading wallet
- Start with small capital ($100)
- Monitor first trades closely

---

## License

MIT License - See LICENSE file
