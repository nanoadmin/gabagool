# Gabagool - Current State (2026-01-27)

## Active Processes

```bash
# Two v5 instances running:
tmux session: monitor_15m_v5           # Conservative
tmux session: monitor_15m_v5_aggressive # Aggressive
```

## v5 Strategy: Liquidity-First Stacking

**Key Innovation**: Zero slippage by design - only trades at BEST ask price level.

v4 Problem: $350 trades caused slippage through order book, turning 1% theoretical profit into 2-3% actual loss.

v5 Solution: Trade only what's available at best price (often $5-30), stack multiple small trades.

**Backtest Results**:
- v4: 8 trades, 1 winner, **-$53.63 PnL**
- v5: 18 trades, 18 winners, **+$2.59 PnL**

## Running Instances

| Instance | Min Margin | Max Trade | tmux Session |
|----------|------------|-----------|--------------|
| `v5` | 0.50% | $100 | `monitor_15m_v5` |
| `aggressive` | 0.20% | $500 | `monitor_15m_v5_aggressive` |

## Key Parameters (v5)

| Setting | v5 (Conservative) | Aggressive |
|---------|-------------------|------------|
| Initial equity | $2,000 | $2,000 |
| Min margin | 0.50% | 0.20% |
| Max trade size | $100 | $500 |
| Max stack/window | 10 | 10 |
| Gas cost | $0.003/tx | $0.003/tx |
| Scan interval | 500ms | 500ms |

## Data Files

**Per-instance** (in `data/logs/`):
- `{instance}_paper_trades.jsonl` - Trade history
- `{instance}_paper_pnl_summary.json` - Equity & stats

**Shared**:
- `opportunities_*.jsonl` - Detected opportunities
- `market_snapshots_*.jsonl` - Raw data (8h retention)

## Quick Commands

```bash
# List sessions
tmux list-sessions

# Attach to session
tmux attach -t monitor_15m_v5
tmux attach -t monitor_15m_v5_aggressive

# Check P&L
cat data/logs/v5_paper_pnl_summary.json | jq
cat data/logs/aggressive_paper_pnl_summary.json | jq

# Start new instance
source .venv/bin/activate
python scripts/monitor_15m_v5.py --instance mytest --fresh --min-margin 0.003

# Kill instance
tmux kill-session -t monitor_15m_v5
```

## Recent Changes (2026-01-27)

1. Created monitor_v5 with liquidity-first stacking strategy
2. Fixed order book parsing bug (API returns asks descending)
3. Added multi-instance support (--instance, --fresh flags)
4. Updated gas cost to realistic Polygon estimate ($0.003/tx)
5. Added Discord webhook for weekly reports
6. Shut down v4, running two v5 instances

## Polygon Gas Analysis

| Scenario | Per Order | 2 Orders (Arb) |
|----------|-----------|----------------|
| Normal | $0.002 | $0.004 |
| Busy | $0.004 | $0.008 |
| Config | $0.003 | $0.006 |

Break-even at 1% margin: ~$0.60 trade size
