# Gabagool Bot - Operations Runbook

## Daily Operations

### Start Bot

```bash
cd /home/botuser/bots/gabagool
source venv/bin/activate
screen -S gabagool
python -m src.main
# Ctrl+A, D to detach
```

### Monitor Bot

```bash
# View live logs
tail -f logs/gabagool.log

# Reattach to session
screen -r gabagool

# Quick status
sqlite3 gabagool.db "SELECT COUNT(*) as positions FROM positions WHERE resolved=0;"
```

### Stop Bot

```bash
screen -r gabagool
# Ctrl+C to stop
# or
screen -X -S gabagool quit
```

## Health Checks

### Every Hour
- [ ] Bot still running (`screen -ls`)
- [ ] Recent log entries
- [ ] No error spikes

### Every Day
- [ ] Review stats summary
- [ ] Check wallet balance
- [ ] Review any failed trades

### Every Week
- [ ] Backup database
- [ ] Review performance metrics
- [ ] Adjust parameters if needed

## Troubleshooting

### Bot Stopped

1. Check screen session: `screen -ls`
2. Check logs for errors: `tail -100 logs/gabagool.log`
3. Restart bot

### No Trades Executing

1. Check market availability (15-min markets)
2. Verify thresholds aren't too strict
3. Check wallet balance
4. Verify API connection

### High Error Rate

1. Check API rate limits
2. Review recent log errors
3. Check network connectivity
4. Pause and investigate

### Incomplete Pairs

1. Check position tracker for stuck positions
2. Review holding time limits
3. Consider manual intervention if needed

## Emergency Procedures

### Stop All Trading

```bash
# Kill bot immediately
pkill -f "python -m src.main"

# Or graceful stop
screen -r gabagool
# Ctrl+C
```

### Recover from Crash

```bash
# Check database state
sqlite3 gabagool.db "SELECT * FROM positions WHERE resolved=0;"

# Restart bot (will load unresolved positions)
screen -S gabagool
python -m src.main
```

### Manual Position Close

If bot can't close positions:

1. Log into Polymarket web UI
2. Find the market
3. Sell positions manually
4. Update database if needed

### Wallet Emergency

If wallet compromised:
1. Stop bot immediately
2. Transfer remaining funds to safe wallet
3. Generate new wallet
4. Update .env
5. Do NOT restart until secure

## Scaling Checklist

### Before Adding Capital

- [ ] Current win rate > 70%
- [ ] No critical errors in past week
- [ ] Understand current position sizing
- [ ] Have plan for increased exposure

### Scaling Steps

1. Update `max_total_exposure` in config
2. Update `max_position_per_market`
3. Consider increasing `max_concurrent_arbitrages`
4. Restart bot
5. Monitor closely for 24 hours

## Backup Procedures

### Database Backup

```bash
# Manual backup
cp gabagool.db gabagool_backup_$(date +%Y%m%d).db

# Automated (add to cron)
0 0 * * * cp /path/to/gabagool.db /path/to/backups/gabagool_$(date +\%Y\%m\%d).db
```

### Config Backup

```bash
# Backup config (excluding secrets)
cp config/default.yaml config/default.yaml.backup
cp config/production.yaml config/production.yaml.backup
```

## Metrics to Track

| Metric | Target | Action if Below |
|--------|--------|-----------------|
| Win Rate | >70% | Review thresholds |
| Avg Profit/Trade | >$0.05 | Increase margins |
| Daily Trades | >5 | Loosen thresholds |
| Error Rate | <5% | Investigate errors |
| Fill Rate | >90% | Check liquidity |

## Contact & Escalation

For issues beyond this runbook:
1. Check samples/ for reference implementations
2. Review Polymarket documentation
3. Check API status pages
