# TradeBot (monorepo)

## Layout
- `bot/`: Python bot
- `trade-engine/`: vendored Polymarket trade engine snapshot (Bun/TypeScript)

## VPS Deployment

### Clone
```bash
git clone <your-repo-url> TradeBot
cd TradeBot
```

### First-time script permissions
```bash
chmod +x deploy/start.sh deploy/stop.sh deploy/restart.sh
```

### Start in paper mode
```bash
bash deploy/start.sh
```

The start script installs `trade-engine` dependencies with `bun install`, installs Python requirements if `requirements.txt` exists, creates `logs/`, and starts the bot in the background in PAPER mode.

### Stop
```bash
bash deploy/stop.sh
```

### Restart
```bash
bash deploy/restart.sh
```

### Check logs
```bash
tail -f logs/bot.log
```

## Paper vs live mode

- **Paper (default)**:

```bash
python bot/main.py
```

- **Live (explicit opt-in)**:

```bash
set TRADEBOT_CONFIRM_LIVE=YES
python bot/main.py --live
```

