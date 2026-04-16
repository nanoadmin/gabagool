# Gabagool Bot

**Money House Paper Trading Dashboard**

*Version 1.1 - Paper-only admin build*
*Updated: 2026-04-16*

---

## Overview

This fork keeps the market-scanning idea from the original Gabagool project, but the deployed app is now a fake-money admin console. It reads market metadata, simulates paired YES/NO arbitrage with a virtual bankroll, records paper positions, and exposes a protected dashboard plus JSON API.

What changed:
- No wallet keys are required for the dashboard runtime
- No real order placement is used in the deployed service
- A FastAPI admin app serves the UI and API
- Unit test results are stored and shown in the admin page
- If live Gamma data is unavailable, the app falls back to demo market data so the UI still works

The original live-trading code remains in the repo for reference, but the `money.daveshouse.xyz` deployment is paper-only.

### Paper Trading Concept

```
Find cheap YES/NO pair -> Buy both with fake money -> Track expected payout -> Settle to virtual balance
```

**Example:**
- Buy YES @ $0.48 avg
- Buy NO @ $0.45 avg
- Pair cost: $0.93
- Payout: $1.00
- Profit: $0.07 (7.5% per pair)

### Admin Dashboard

The dashboard includes:
- fake bankroll, equity, realized and open P&L
- current 15-minute market opportunity board
- open and settled paper positions
- service journal/logs
- latest unit test results
- operator controls for scan now, run tests, and reset bankroll

### Target Markets

- 15-minute BTC up/down markets
- 15-minute ETH up/down markets
- 15-minute SOL up/down markets
- ~96 markets per day (BTC alone)

---

## Architecture

This fork has two runtime paths:

1. Legacy live bot code
   Present for reference and future strategy work.

2. Money House paper dashboard
   The deployed path for `money.daveshouse.xyz`.

The paper dashboard uses:
- `src/gamma_client.py` for public market discovery
- `src/paper_service.py` for fake-money state, scans, settlement, and test execution
- `src/admin_app.py` for the FastAPI API and static dashboard hosting
- `src/admin_static/` for the admin UI

## Project Structure

---

```
gabagool/
├── config/                  # Legacy bot config and paper env example
├── deploy/                  # Systemd + Caddy deployment snippets
├── src/                     # Bot, paper service, FastAPI app, static admin UI
├── tests/                   # Unit and legacy live tests
├── backtest/                # Legacy paper/simulation scripts
└── requirements.txt         # Python dependencies
```

---

## Quick Start

```bash
# 1. Setup environment
cd gabagool
make setup

# 2. Optional paper settings
cp config/paper_dashboard.env.example .env

# 3. Run the admin app locally
make dashboard

# 4. Open the UI
# http://127.0.0.1:18111
```

---

## Paper Dashboard Configuration

The paper dashboard reads these environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONEY_PORT` | `18111` | Local admin port |
| `MONEY_SCAN_INTERVAL` | `15` | Seconds between scans |
| `MONEY_STARTING_BALANCE` | `1000` | Starting fake bankroll |
| `MONEY_MAX_TRADE_STAKE` | `120` | Max fake stake per opportunity |
| `MONEY_TARGET_ASSETS` | `BTC,ETH,SOL` | Assets to scan |

---

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - System design
- [SETUP.md](docs/SETUP.md) - Installation guide
- [PARAMETERS.md](docs/PARAMETERS.md) - Configuration reference
- [RUNBOOK.md](docs/RUNBOOK.md) - Operations guide
- [deploy/systemd/money-house.service](deploy/systemd/money-house.service) - Example systemd unit
- [deploy/Caddyfile.money-house](deploy/Caddyfile.money-house) - Example Caddy site block

---

## Safety

- Never delete data or code
- The deployed dashboard is fake-money only
- Do not provide wallet keys to the paper dashboard
- Live bot commands are still in the repo but are not used for `money.daveshouse.xyz`
- Treat `make run-live` as separate legacy functionality

---

## License

MIT License - See LICENSE file
