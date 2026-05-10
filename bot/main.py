import asyncio
import json
import os
from pathlib import Path
import sys
import time

import httpx

import config
from price_feed import poll_prices
from polymarket_book import BookReader
from strategy import compute_signals, decide
from execution import handle_decision
from logger import log_metrics


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


def _spread_bps(book: dict, direction: str):
    side = book.get("up") if direction == "UP" else book.get("down")
    if not isinstance(side, dict):
        return None

    bids = side.get("bids")
    asks = side.get("asks")
    if not isinstance(bids, list) or not bids:
        return None
    if not isinstance(asks, list) or not asks:
        return None

    try:
        bid_px = float(bids[0][0])
        ask_px = float(asks[0][0])
    except Exception:
        return None

    mid_price = (bid_px + ask_px) / 2.0
    if mid_price <= 0:
        return None

    spread = max(0.0, ask_px - bid_px)
    return (spread / mid_price) * 10000.0


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    trade_engine_dir = repo_root / "trade-engine"
    _load_dotenv(str(repo_root / ".env"))
    live = "--live" in sys.argv
    mode = "live" if live else "paper"
    print(f"MODE: {'LIVE' if live else 'PAPER'}", flush=True)
    print(f"STRATEGY MODE: {config.STRATEGY_VERSION}", flush=True)
    print(f"STRATEGY VERSION: {config.STRATEGY_VERSION}", flush=True)

    # Ensure wrapper subprocesses inherit credentials in live mode.
    if live:
        confirm = os.environ.get("TRADEBOT_CONFIRM_LIVE", "").strip().upper()
        if confirm != "YES":
            raise RuntimeError(
                'Live mode requires explicit confirmation. Set env var TRADEBOT_CONFIRM_LIVE="YES" and rerun with --live.'
            )
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

    # Persist bot state across markets (pnl etc.)
    state: dict = {"position": None, "pnl": 0.0, "cooldown_until": 0.0, "trades": 0}

    async with httpx.AsyncClient() as client:
        while True:
            # ---- Discover a fresh market ----
            while True:
                data = None
                for attempt in range(1, 4):
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        cwd=str(trade_engine_dir),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

                    try:
                        stdout_b, stderr_b = await asyncio.wait_for(
                            proc.communicate(), timeout=15.0
                        )
                    except TimeoutError:
                        proc.kill()
                        await proc.communicate()
                        print(
                            f"Discover timeout attempt {attempt}/3",
                            file=sys.stderr,
                            flush=True,
                        )
                        if attempt < 3:
                            await asyncio.sleep(1.0)
                            continue
                        print("Discover failed after retries", file=sys.stderr, flush=True)
                        await asyncio.sleep(1.0)
                        break

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
                        raise RuntimeError(
                            f"failed to parse JSON from wrapper stdout: {e}"
                        ) from e

                    break

                if data is None:
                    continue

                now_ts = time.time()
                market_age = now_ts - (float(data["open_time_ms"]) / 1000.0)

                if market_age > 60.0:
                    print(f"Skipping late market (age={int(market_age)}s)", flush=True)
                    next_slot_ts = (int(now_ts // 300) + 1) * 300
                    wait_time = next_slot_ts - now_ts
                    if wait_time > 0:
                        print(
                            f"Waiting {int(wait_time)}s for next market slot...",
                            flush=True,
                        )
                        await asyncio.sleep(wait_time + 1)
                    continue

                print(f"Using market (age={int(market_age)}s)", flush=True)
                break

            print("DISCOVERED MARKET")
            print(json.dumps(data, indent=2, sort_keys=True), flush=True)

            token_ids = data.get("clob_token_ids")
            if not isinstance(token_ids, list) or len(token_ids) < 2:
                raise RuntimeError("discover output missing clob_token_ids")

            resolution_time_s = float(data["resolution_time_ms"]) / 1000.0
            state["market"] = {
                "market_id": str(data.get("market_id", "")),
                "resolution_time_s": float(resolution_time_s),
            }
            # Treat MAX_TRADES as per-market cap; reset trade counter for new market.
            state["trades"] = 0

            # ---- Start a fresh book for this market ----
            book = BookReader()
            await book.start([str(token_ids[0]), str(token_ids[1])])

            last_decision: str | None = None
            last_log_ts: float = 0.0
            last_metrics_ts: float = 0.0

            # ---- Trade this market until it resolves ----
            while True:
                try:
                    now_ts = time.time()
                    if now_ts >= float(state["market"]["resolution_time_s"]):
                        print("Market resolved, switching to next market...", flush=True)
                        state["position"] = None
                        state["cooldown_until"] = 0.0
                        state["entry_cooldown_until"] = 0.0
                        state["exit_cooldown_until"] = 0.0
                        state["_signal_ctx"] = {}
                        state["trades"] = 0
                        break

                    prices = await poll_prices(client)
                    snap = book.latest() or {}

                    signals = compute_signals(prices, snap or {}, data, now_ts)
                    age_s = float(now_ts - (float(data["open_time_ms"]) / 1000.0))
                    state["_signal_ctx"] = {
                        "divergence": float(signals.get("divergence", 0.0)),
                        "imbalance": float(signals.get("imbalance", 0.0)),
                        "age": float(age_s),
                    }
                    decision_note: str | None = None

                    if now_ts < float(state.get("cooldown_until", 0.0)):
                        d = {"action": "SKIP"}
                    elif state.get("position") is not None:
                        d = {"action": "SKIP"}
                    else:
                        d_raw = decide(signals, data, now_ts)
                        EPSILON = 1e-6
                        if (
                            d_raw.get("action") == "ENTER"
                            and (
                                age_s < float(config.MIN_ENTRY_AGE)
                                or age_s > float(config.MAX_ENTRY_AGE)
                            )
                        ):
                            d = {"action": "SKIP"}
                            decision_note = f"age out of range: {int(age_s)}s"
                        elif (
                            d_raw.get("action") == "ENTER"
                            and d_raw.get("direction") == "UP"
                        ):
                            d = {"action": "SKIP"}
                            decision_note = "UP trades disabled"
                        elif (
                            d_raw.get("action") == "ENTER"
                            and d_raw.get("direction") == "DOWN"
                            and float(signals.get("imbalance", 0.0))
                            > float(config.MIN_IMBALANCE) + EPSILON
                        ):
                            d = {"action": "SKIP"}
                            decision_note = (
                                f"imbalance too weak: {float(signals.get('imbalance', 0.0)):.2f}"
                            )
                        elif (
                            d_raw.get("action") == "ENTER"
                            and d_raw.get("direction") in ("UP", "DOWN")
                            and (spread_bps := _spread_bps(snap or {}, str(d_raw.get("direction"))))
                            is not None
                            and spread_bps > float(config.MAX_SPREAD_BPS) + EPSILON
                        ):
                            d = {"action": "SKIP"}
                            decision_note = f"spread too wide: {spread_bps:.0f} bps"
                        else:
                            d = d_raw

                    if d.get("action") == "ENTER" and d.get("direction") in ("UP", "DOWN"):
                        decision_str = f"ENTER_{d['direction']}"
                    else:
                        decision_str = "SKIP"
                        if decision_note:
                            decision_str = f"SKIP ({decision_note})"

                    should_log = False
                    if decision_str != last_decision:
                        should_log = True
                    elif now_ts - last_log_ts >= 5.0:
                        should_log = True

                    if should_log:
                        ts = time.strftime("%H:%M:%S", time.localtime(now_ts))
                        age_s_i = int(age_s)
                        print(
                            f"[{ts}] age={age_s_i}s div={signals['divergence']:.2f} imb={signals['imbalance']:.2f} decision={decision_str}",
                            flush=True,
                        )
                        last_decision = decision_str
                        last_log_ts = now_ts

                    if now_ts - last_metrics_ts >= 5.0:
                        total_pnl = float(state.get("pnl", 0.0))
                        pos = state.get("position")
                        current_pnl = 0.0
                        if isinstance(pos, dict) and pos.get("direction") in ("UP", "DOWN"):
                            try:
                                side_key = "up" if pos["direction"] == "UP" else "down"
                                side = snap.get(side_key) if isinstance(snap, dict) else None
                                bids = side.get("bids") if isinstance(side, dict) else None
                                if isinstance(bids, list) and bids:
                                    cur_px = float(bids[0][0])
                                    current_pnl = (cur_px - float(pos["entry_price"])) * float(
                                        pos["size"]
                                    )
                            except Exception:
                                current_pnl = 0.0
                        log_metrics(
                            {
                                "ts": float(now_ts),
                                "mode": mode,
                                "has_position": state.get("position") is not None,
                                "current_pnl": float(current_pnl),
                                "total_pnl": float(total_pnl),
                            }
                        )
                        last_metrics_ts = now_ts

                    await handle_decision(d, snap, data, state, now_ts, mode=mode)
                except Exception as e:
                    print(f"PRICE ERROR: {e}", file=sys.stderr, flush=True)
                await asyncio.sleep(1.0)

            await book.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

