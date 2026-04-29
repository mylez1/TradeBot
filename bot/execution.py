import asyncio
import json
import math
from pathlib import Path
import sys
import time

import config
from logger import log_trade

# Must match @polymarket/clob-client ROUNDING_CONFIG *.size (all ticks use size=2 → 0.01 share step).
_SHARE_STEP = 0.01


def _quantize_price(px: float) -> float:
    """Match clob-client price decimals for default tick 0.01 (2dp)."""
    return round(px + 1e-12, 2)


def _quantize_buy_shares(min_shares: float) -> float:
    """Round up to 0.01 share grid — SDK applies roundDown(size, 2) before signing."""
    return math.ceil(min_shares / _SHARE_STEP - 1e-9) * _SHARE_STEP


def _quantize_exit_shares(shares: float) -> float:
    """Floor to 0.01 share grid (same as PolymarketEarlyBirdClient quantize for sells)."""
    return math.floor(float(shares) / _SHARE_STEP + 1e-9) * _SHARE_STEP


async def _call_order_wrapper(mode: str, orders: list[dict]) -> dict:
    trade_engine_dir = Path(__file__).resolve().parents[1] / "trade-engine"
    payload = {"mode": mode, "orders": orders}
    proc = await asyncio.create_subprocess_exec(
        "bun",
        "run",
        "scripts/py_bridge_order.ts",
        cwd=str(trade_engine_dir),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_b, stderr_b = await proc.communicate(
        json.dumps(payload).encode("utf-8")
    )

    stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    if stderr:
        print(stderr, file=sys.stderr, flush=True)

    stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"order wrapper failed (code {proc.returncode})")
    if not stdout:
        raise RuntimeError("order wrapper returned empty stdout")
    return json.loads(stdout)


async def handle_decision(
    decision: dict, book: dict, market: dict, state: dict, now_ts: float, *, mode: str
) -> None:
    """
    Mutates `state` in-place.

    state:
      {
        "position": None | {direction, token_id, entry_price, size, entry_time},
        "pnl": float,
        "cooldown_until": float
      }
    """
    state.setdefault("position", None)
    state.setdefault("pnl", 0.0)
    state.setdefault("cooldown_until", 0.0)
    state.setdefault("entry_cooldown_until", 0.0)
    state.setdefault("exit_cooldown_until", 0.0)
    state.setdefault("trades", 0)
    state.setdefault("_signal_ctx", {})

    position = state["position"]
    cooldown_until = float(state["cooldown_until"])
    entry_cooldown_until = float(state["entry_cooldown_until"])
    exit_cooldown_until = float(state["exit_cooldown_until"])
    signal_ctx = state.get("_signal_ctx") if isinstance(state.get("_signal_ctx"), dict) else {}

    # --- Helpers to read best bid/ask ---
    def best_ask(direction: str) -> float | None:
        side = book.get("up") if direction == "UP" else book.get("down")
        if not isinstance(side, dict):
            return None
        asks = side.get("asks")
        if not isinstance(asks, list) or not asks:
            return None
        return float(asks[0][0])

    def best_ask_size(direction: str) -> float:
        side = book.get("up") if direction == "UP" else book.get("down")
        if not isinstance(side, dict):
            return 0.0
        asks = side.get("asks")
        if not isinstance(asks, list) or not asks:
            return 0.0
        try:
            return float(asks[0][1])
        except Exception:
            return 0.0

    def best_bid(direction: str) -> float | None:
        side = book.get("up") if direction == "UP" else book.get("down")
        if not isinstance(side, dict):
            return None
        bids = side.get("bids")
        if not isinstance(bids, list) or not bids:
            return None
        return float(bids[0][0])

    # --- Exit logic ---
    if position is not None:
        if now_ts <= exit_cooldown_until:
            return

        # Hold if < 60s to resolution
        resolution_s = float(market["resolution_time_ms"]) / 1000.0
        if resolution_s - now_ts < 60.0:
            return

        cur = best_bid(position["direction"])
        if cur is None:
            return

        entry_px = float(position["entry_price"])
        tp = min(0.99, entry_px + 0.05)
        sl = max(0.01, entry_px - 0.05)
        should_exit = cur >= tp or cur <= sl
        if should_exit:
            token_id = position["token_id"]
            px_exit = _quantize_price(float(cur))
            size = _quantize_exit_shares(float(position["size"]))
            if size <= 0:
                return
            resp = await _call_order_wrapper(
                mode,
                [
                    {
                        "tokenId": token_id,
                        "action": "sell",
                        "price": px_exit,
                        "shares": size,
                        "orderType": "FOK",
                    }
                ],
            )
            placed = (resp.get("placed") or [{}])[0]
            if not placed.get("success") or not placed.get("orderId"):
                print(
                    f"[{time.strftime('%H:%M:%S')}] EXIT FAILED: {placed.get('errorMsg','')}",
                    file=sys.stderr,
                    flush=True,
                )
                state["exit_cooldown_until"] = now_ts + 2.0
                return

            entry = float(position["entry_price"])
            pnl = (px_exit - entry) * size
            state["pnl"] = float(state["pnl"]) + pnl
            state["position"] = None
            state["cooldown_until"] = now_ts + 30.0
            state["exit_cooldown_until"] = 0.0
            try:
                market_id = str(market.get("market_id", ""))
                hold_seconds = max(0.0, float(now_ts) - float(position.get("entry_time", now_ts)))
                log_trade(
                    {
                        "ts": float(now_ts),
                        "mode": mode,
                        "event": "EXIT",
                        "market_id": market_id,
                        "direction": str(position["direction"]),
                        "entry_price": float(entry),
                        "exit_price": float(px_exit),
                        "size": float(size),
                        "pnl": float(pnl),
                        "result": "WIN" if pnl >= 0 else "LOSS",
                        "hold_seconds": float(hold_seconds),
                        "divergence": float(
                            position.get("divergence", signal_ctx.get("divergence", 0.0))
                        ),
                        "imbalance": float(
                            position.get("imbalance", signal_ctx.get("imbalance", 0.0))
                        ),
                        "age": float(position.get("age", signal_ctx.get("age", 0.0))),
                    }
                )
            except Exception:
                pass
            print(
                f"EXIT {position['direction']} @{px_exit:.2f} (size={size:.2f}) pnl={pnl:+.2f} total_pnl={state['pnl']:+.2f} orderId={placed.get('orderId')}",
                flush=True,
            )
        return

    # --- Entry logic ---
    if now_ts < cooldown_until:
        return

    if (
        decision.get("action") == "ENTER"
        and state["position"] is None
        and now_ts > cooldown_until
        and now_ts > entry_cooldown_until
    ):
        if int(state["trades"]) >= int(config.MAX_TRADES):
            return

        direction = decision.get("direction")
        if direction not in ("UP", "DOWN"):
            return
        if direction == "UP":
            return

        token_ids = market.get("clob_token_ids")
        if not isinstance(token_ids, list) or len(token_ids) < 2:
            return
        token_id = str(token_ids[0] if direction == "UP" else token_ids[1])

        px_raw = best_ask(direction)
        if px_raw is None:
            return
        px = _quantize_price(float(px_raw))
        if not (0 < px < 1):
            return

        max_fillable = best_ask_size(direction)
        if max_fillable <= 0:
            return

        upper = min(float(config.MAX_TRADE_SIZE), float(max_fillable))
        min_notional = float(config.MIN_ORDER_NOTIONAL_USD)
        lower = _quantize_buy_shares(min_notional / px)
        while lower * px < min_notional - 1e-6:
            lower = round(lower + _SHARE_STEP, 2)

        if upper < lower:
            print(
                f"[{time.strftime('%H:%M:%S')}] ENTRY SKIPPED: need ≥{lower:.2f} shares (~${min_notional:.2f} @ {px:.2f}); "
                f"cap/liquidity allows {upper:.2f} (raise MAX_TRADE_SIZE or wait for liquidity)",
                flush=True,
            )
            return

        size = lower

        # After ANY entry attempt (success or failure), block new entries briefly.
        state["entry_cooldown_until"] = now_ts + 5.0

        try:
            resp = await _call_order_wrapper(
                mode,
                [
                    {
                        "tokenId": token_id,
                        "action": "buy",
                        "price": px,
                        "shares": size,
                        "orderType": "FOK",
                    }
                ],
            )
        except Exception as e:
            print(
                f"[{time.strftime('%H:%M:%S')}] ENTRY FAILED: {e}",
                file=sys.stderr,
                flush=True,
            )
            try:
                log_trade(
                    {
                        "ts": float(now_ts),
                        "mode": mode,
                        "event": "ENTRY_FAILED",
                        "reason": str(e),
                    }
                )
            except Exception:
                pass
            return

        placed = (resp.get("placed") or [{}])[0]
        if not placed.get("success") or not placed.get("orderId"):
            print(
                f"[{time.strftime('%H:%M:%S')}] ENTRY FAILED: {placed.get('errorMsg','')}",
                file=sys.stderr,
                flush=True,
            )
            try:
                log_trade(
                    {
                        "ts": float(now_ts),
                        "mode": mode,
                        "event": "ENTRY_FAILED",
                        "reason": str(placed.get("errorMsg", "")),
                    }
                )
            except Exception:
                pass
            return

        state["position"] = {
            "direction": direction,
            "token_id": token_id,
            "entry_price": float(px),
            "size": float(size),
            "entry_time": float(now_ts),
            "divergence": float(signal_ctx.get("divergence", 0.0)),
            "imbalance": float(signal_ctx.get("imbalance", 0.0)),
            "age": float(signal_ctx.get("age", 0.0)),
        }
        state["trades"] = int(state["trades"]) + 1
        try:
            market_id = str(market.get("market_id", ""))
            log_trade(
                {
                    "ts": float(now_ts),
                    "mode": mode,
                    "event": "ENTRY",
                    "market_id": market_id,
                    "direction": direction,
                    "price": float(px),
                    "size": float(size),
                    "divergence": float(signal_ctx.get("divergence", 0.0)),
                    "imbalance": float(signal_ctx.get("imbalance", 0.0)),
                    "age": float(signal_ctx.get("age", 0.0)),
                }
            )
        except Exception:
            pass
        print(
            f"ENTRY {direction} @{px:.2f} (size={size:.2f}) orderId={placed.get('orderId')}",
            flush=True,
        )





























































