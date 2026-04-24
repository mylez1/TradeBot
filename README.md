# TradeBot (monorepo)

## Layout
- `bot/`: Python bot
- `trade-engine/`: vendored Polymarket trade engine snapshot (Bun/TypeScript)

## VPS quickstart

### Install
```bash
cd trade-engine
bun install
```

### Run (live)
```bash
cd ..
python bot/main.py --live
```

### Optional helper
```bash
bash deploy/start.sh
```

