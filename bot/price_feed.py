import asyncio
import time

import httpx


async def poll_prices(client: httpx.AsyncClient) -> dict:
    backoffs = (0.2, 0.5, 1.0)
    timeout_s = 2.0

    async def get_json(url: str) -> dict:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.get(url, timeout=timeout_s)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_exc = e if isinstance(e, Exception) else Exception(str(e))
                if attempt < 2:
                    await asyncio.sleep(backoffs[attempt])
        raise RuntimeError(f"request failed after 3 attempts: {url}") from last_exc

    binance_url = (
        "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"
    )
    coinbase_url = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"

    b_json, c_json = await asyncio.gather(
        get_json(binance_url),
        get_json(coinbase_url),
    )

    now = time.time()

    b_bid = float(b_json["bidPrice"])
    b_ask = float(b_json["askPrice"])
    b_mid = (b_bid + b_ask) / 2.0

    c_bid = float(c_json["bid"])
    c_ask = float(c_json["ask"])
    c_mid = (c_bid + c_ask) / 2.0

    return {
        "ts": now,
        "binance": {"bid": b_bid, "ask": b_ask, "mid": b_mid, "ts": now},
        "coinbase": {"bid": c_bid, "ask": c_ask, "mid": c_mid, "ts": now},
    }

