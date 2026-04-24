#!/usr/bin/env bun
/**
 * One-shot order execution bridge for Python.
 *
 * Input JSON: from --request or stdin
 * {
 *   "mode": "paper" | "live",
 *   "dry_run"?: boolean,
 *   "orders": [{ "tokenId": "...", "action": "buy"|"sell", "price": 0.62, "shares": 10, "orderType"?: "GTC"|"FOK" }]
 * }
 *
 * Output JSON (stdout only):
 * { "placed": [{ "orderId": "...", "success": true, "errorMsg": "" }] }
 */

import {
  EarlyBirdSimClient,
  PolymarketEarlyBirdClient,
  type BookSnapshot,
  type EarlyBirdClient,
} from "../engine/client";
import { OrderBook } from "../tracker/orderbook";

function getArg(name: string): string | undefined {
  const idx = process.argv.indexOf(name);
  if (idx === -1) return undefined;
  return process.argv[idx + 1];
}

async function readStdin(): Promise<string> {
  const chunks: Uint8Array[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk as Uint8Array);
  }
  return Buffer.concat(chunks).toString("utf-8");
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

async function main() {
  try {
    const requestArg = getArg("--request");
    const raw = (requestArg ?? (await readStdin())).trim();
    if (!raw) throw new Error("empty request JSON (stdin or --request required)");

    const req = JSON.parse(raw) as any;
    const mode = req?.mode;
    const dryRun = Boolean(req?.dry_run);
    const orders = req?.orders;
    if (mode !== "paper" && mode !== "live") {
      throw new Error('mode must be "paper" or "live"');
    }
    if (!dryRun && (!Array.isArray(orders) || orders.length === 0)) {
      throw new Error("orders must be a non-empty array (unless dry_run=true)");
    }

    // Collect token ids referenced by the orders.
    const tokenIds = Array.from(
      new Set<string>((orders ?? []).map((o: any) => String(o.tokenId))),
    );

    // Subscribe orderbook so we can obtain tickSize + feeRateBps.
    // OrderBook expects the UP/DOWN pair; for v1 we assume the first two unique
    // tokenIds correspond to the market pair (UP, DOWN). This matches our bot.
    const pair = tokenIds.length >= 2 ? [tokenIds[0]!, tokenIds[1]!] : [tokenIds[0]!, tokenIds[0]!];
    const orderBook = new OrderBook();
    orderBook.subscribe(pair);

    // Wait briefly for tick_size / fee_rate and book best levels to populate.
    const start = Date.now();
    while (Date.now() - start < 10_000) {
      const snap: any = orderBook.getSnapshotData();
      const ready =
        snap?.up &&
        Array.isArray(snap.up.bids) &&
        Array.isArray(snap.up.asks) &&
        snap.up.bids.length > 0 &&
        snap.up.asks.length > 0 &&
        snap?.down &&
        Array.isArray(snap.down.bids) &&
        Array.isArray(snap.down.asks) &&
        snap.down.bids.length > 0 &&
        snap.down.asks.length > 0;
      if (ready) break;
      await sleep(200);
    }

    const getBookSnapshot = (tokenId: string): BookSnapshot => {
      const side =
        tokenId === pair[0] ? ("UP" as const) : ("DOWN" as const);
      const ask = orderBook.bestAskInfo(side);
      const bid = orderBook.bestBidInfo(side);
      return {
        bestAsk: ask?.price ?? null,
        bestAskLiquidity: ask?.liquidity ?? null,
        bestBid: bid?.price ?? null,
        bestBidLiquidity: bid?.liquidity ?? null,
      };
    };

    let client: EarlyBirdClient;
    if (mode === "paper") {
      client = new EarlyBirdSimClient(getBookSnapshot);
    } else {
      client = new PolymarketEarlyBirdClient();
    }
    await client.init();

    const multiOrders = (orders ?? []).map((o: any) => {
      const tokenId = String(o.tokenId);
      const action = o.action === "sell" ? "sell" : "buy";
      const price = Number(o.price);
      const shares = Number(o.shares);
      const orderType =
        o.orderType === "FOK" || o.orderType === "GTC" ? o.orderType : undefined;
      if (!Number.isFinite(price) || price <= 0 || price >= 1) {
        throw new Error(`invalid price: ${o.price}`);
      }
      if (!Number.isFinite(shares) || shares <= 0) {
        throw new Error(`invalid shares: ${o.shares}`);
      }

      return {
        tokenId,
        action,
        price,
        shares,
        tickSize: orderBook.getTickSize(tokenId),
        feeRateBps: orderBook.getFeeRate(tokenId),
        // v1: engine uses false in lifecycle; keep consistent here.
        negRisk: false,
        orderType,
      };
    });

    const placed = dryRun ? [] : await client.postMultipleOrders(multiOrders);

    const out = {
      placed: placed.map((p) => ({
        orderId: p.orderId,
        success: p.success,
        errorMsg: p.errorMsg,
      })),
    };

    process.stdout.write(JSON.stringify(out));
    process.stdout.write("\n");
    process.exit(0);
  } catch (err: any) {
    const msg = err?.stack || err?.message || String(err);
    process.stderr.write(`ERROR: ${msg}\n`);
    process.exit(1);
  }
}

main();

