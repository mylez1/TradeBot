import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx

from price_feed import poll_prices
from polymarket_book import BookReader
from strategy import compute_signals, decide
from execution import handle_decision


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    trade_engine_dir = repo_root / "trade-engine"
    live = "--live" in sys.argv
    mode = "live" if live else "paper"
    print(f"MODE: {'LIVE' if live else 'PAPER'}", flush=True)

    # Ensure wrapper subprocesses inherit credentials in live mode.
    if live:
        _load_dotenv(str(repo_root / ".env"))
        required = [
            "PRIVATE_KEY",
            "POLY_FUNDER_ADDRESS",
            "BUILDER_KEY",
            "BUILDER_SECRET",
            "BUILDER_PASSPHRASE",
        ]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                "Missing required env vars for live mode: " + ", ".join(missing)
            )
        if not os.environ["PRIVATE_KEY"].startswith("0x"):
            raise RuntimeError("PRIVATE_KEY must be 0x-prefixed")

    cmd = ["bun", "run", "scripts/py_bridge_discover.ts"]

    while True:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(trade_engine_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(f"discover wrapper timed out (>5s): {cmd!r}")

        stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
        stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()

        if stderr:
            print(stderr, file=sys.stderr)

        if proc.returncode != 0:
            raise RuntimeError(
                f"discover wrapper failed with code {proc.returncode}: {cmd!r}"
            )

        if not stdout:
            raise RuntimeError("discover wrapper returned empty stdout")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            print("RAW STDOUT (invalid JSON):", file=sys.stderr)
            print(stdout, file=sys.stderr)
            raise RuntimeError(f"failed to parse JSON from wrapper stdout: {e}") from e

        now_ts = time.time()
        market_age = now_ts - (float(data["open_time_ms"]) / 1000.0)

        if market_age > 60.0:
            print(f"Skipping late market (age={int(market_age)}s)", flush=True)
            next_slot_ts = (int(now_ts // 300) + 1) * 300
            wait_time = next_slot_ts - now_ts
            if wait_time > 0:
                print(f"Waiting {int(wait_time)}s for next market slot...", flush=True)
                await asyncio.sleep(wait_time + 1)
            continue

        print(f"Using market (age={int(market_age)}s)", flush=True)
        break

    print("DISCOVERED MARKET")
    print(json.dumps(data, indent=2, sort_keys=True), flush=True)

    token_ids = data.get("clob_token_ids")
    if not isinstance(token_ids, list) or len(token_ids) < 2:
        raise RuntimeError("discover output missing clob_token_ids")

    book = BookReader()
    await book.start([str(token_ids[0]), str(token_ids[1])])

    last_decision: str | None = None
    last_log_ts: float = 0.0
    state = {"position": None, "pnl": 0.0, "cooldown_until": 0.0, "trades": 0}

    async with httpx.AsyncClient() as client:
        while True:
            try:
                now_ts = time.time()
                prices = await poll_prices(client)
                snap = book.latest() or {}

                signals = compute_signals(prices, snap or {}, data, now_ts)
                d = decide(signals, data, now_ts)
                if d.get("action") == "ENTER" and d.get("direction") in ("UP", "DOWN"):
                    decision_str = f"ENTER_{d['direction']}"
                else:
                    decision_str = "SKIP"

                should_log = False
                if decision_str != last_decision:
                    should_log = True
                elif now_ts - last_log_ts >= 5.0:
                    should_log = True

                if should_log:
                    ts = time.strftime("%H:%M:%S", time.localtime(now_ts))
                    age_s = int(now_ts - (float(data["open_time_ms"]) / 1000.0))
                    print(
                        f"[{ts}] age={age_s}s div={signals['divergence']:.2f} imb={signals['imbalance']:.2f} decision={decision_str}",
                        flush=True,
                    )
                    last_decision = decision_str
                    last_log_ts = now_ts

                await handle_decision(d, snap, data, state, now_ts, mode=mode)
            except Exception as e:
                print(f"PRICE ERROR: {e}", file=sys.stderr, flush=True)
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

