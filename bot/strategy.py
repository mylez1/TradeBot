def compute_signals(prices: dict, book: dict, market: dict, now_ts: float) -> dict:
    """
    Compute divergence + orderbook imbalance and combine into a directional signal.

    Returns:
      {
        "divergence": float,
        "imbalance": float,
        "direction": "UP" | "DOWN" | None,
        "valid": bool
      }
    """
    # --- Divergence (Binance vs Coinbase) ---
    threshold_usd = 7.5  # small threshold (5–10 USD)
    b_mid = float(prices["binance"]["mid"])
    c_mid = float(prices["coinbase"]["mid"])
    delta = b_mid - c_mid

    div_dir: str | None
    if delta > threshold_usd:
        div_dir = "UP"
    elif delta < -threshold_usd:
        div_dir = "DOWN"
    else:
        div_dir = None

    # --- Imbalance (UP side, top 5 levels) ---
    up = (book or {}).get("up") if isinstance(book, dict) else None
    bids = up.get("bids") if isinstance(up, dict) else None
    asks = up.get("asks") if isinstance(up, dict) else None

    bid_sum = 0.0
    ask_sum = 0.0
    if isinstance(bids, list):
        for lvl in bids[:5]:
            try:
                bid_sum += float(lvl[1])
            except Exception:
                continue
    if isinstance(asks, list):
        for lvl in asks[:5]:
            try:
                ask_sum += float(lvl[1])
            except Exception:
                continue

    denom = bid_sum + ask_sum
    imbalance = (bid_sum - ask_sum) / denom if denom > 0 else 0.0

    imb_dir: str | None
    if imbalance > 0.1:
        imb_dir = "UP"
    elif imbalance < -0.1:
        imb_dir = "DOWN"
    else:
        imb_dir = None

    direction = div_dir if (div_dir is not None and div_dir == imb_dir) else None
    valid = direction is not None

    return {
        "divergence": float(delta),
        "imbalance": float(imbalance),
        "direction": direction,
        "valid": bool(valid),
    }


def decide(signals: dict, market: dict, now_ts: float) -> dict:
    open_time_ms = float(market["open_time_ms"])
    open_time_s = open_time_ms / 1000.0
    market_age = now_ts - open_time_s

    if not (60.0 <= market_age <= 180.0):
        return {"action": "SKIP"}

    if signals.get("valid"):
        return {"action": "ENTER", "direction": signals.get("direction")}

    return {"action": "SKIP"}

