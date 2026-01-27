# Gabagool - Current State (2026-01-27)

## Active Process

```bash
tmux session: gabagool_v4
Script: scripts/monitor_15m_v4.py
Status: Running (paper trading mode)
```

## What It Does

Monitors Polymarket 15-minute crypto prediction markets (BTC, ETH, SOL, XRP) for arbitrage opportunities. When `combined_ask < 1.00`, opens paper trades.

## Key Parameters

| Setting | Value |
|---------|-------|
| Initial equity | $2,000 |
| Trade size | $200-$500 (scales with margin) |
| Daily loss limit | 20% ($400) |
| Max positions | 4 |
| Scan interval | 500ms |
| Data retention | 8 hours (snapshots only) |

## Data Files

**Permanent** (in `data/logs/`):
- `paper_trades.jsonl` - Trade history
- `paper_pnl_summary.json` - Equity & stats
- `opportunities_*.jsonl` - Detected opportunities

**Auto-deleted**:
- `market_snapshots_*.jsonl` - Raw data (8h retention)

## Quick Commands

```bash
# Status
tmux attach -t gabagool_v4   # Ctrl+B, D to detach
cat data/logs/paper_pnl_summary.json | jq

# Restart
tmux kill-session -t gabagool_v4
cd ~/bots/gabagool && tmux new-session -d -s gabagool_v4 \
  "source .venv/bin/activate && python scripts/monitor_15m_v4.py 2>&1 | tee logs/monitor_v4.log"
```

## Recent Changes (2026-01-27)

1. Deleted 21GB unused goldsky data
2. Created monitor_v4 with paper trading
3. Added equity tracking, position sizing, risk limits

## Next Steps (Pending Discussion)

- Re-entry strategies
- Early exit before resolution
- Execution delay simulation
- Leg risk handling refinement
