#!/usr/bin/env bun
/**
 * Stream Polymarket order book snapshots (NDJSON) for Python consumption.
 *
 * stdout: JSON lines only
 * stderr: debug/errors allowed
 *
 * Usage:
 *   bun run scripts/py_bridge_book.ts --token-ids '["id1","id2"]' [--depth 5] [--interval-ms 1000]
 */

import { OrderBook } from "../tracker/orderbook";

function getArg(name: string): string | undefined {
  const idx = process.argv.indexOf(name);
  if (idx === -1) return undefined;
  return process.argv[idx + 1];
}

function getIntArg(name: string, def: number): number {
  const v = getArg(name);
  if (!v) return def;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : def;
}

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

async function main() {
  try {
    const tokenIdsRaw = getArg("--token-ids");
    if (!tokenIdsRaw) {
      throw new Error('missing required arg: --token-ids \'["id1","id2"]\'');
    }
    process.stderr.write(`book: tokenIdsRaw=${tokenIdsRaw}\n`);

    // Robust parsing: Windows shells can strip inner quotes, turning the array
    // into numeric JSON. These token IDs exceed JS Number precision, so we must
    // NOT accept numeric parses. Prefer JSON.parse only when it yields strings;
    // otherwise extract digit runs.
    let tokenIds: string[] = [];
    try {
      const parsed = JSON.parse(tokenIdsRaw);
      if (Array.isArray(parsed)) {
        const allStrings = parsed.every((x) => typeof x === "string");
        if (allStrings) {
          tokenIds = parsed as string[];
        }
      }
    } catch {
      // ignore; we'll fall back to regex extraction
    }

    if (tokenIds.length < 2) {
      // First try digit runs (real token IDs are long integers).
      const digitMatches = tokenIdsRaw.match(/\d{10,}/g) || [];
      if (digitMatches.length >= 2) tokenIds = [digitMatches[0]!, digitMatches[1]!];
    }

    if (tokenIds.length < 2) {
      // Last resort: parse bracketed comma-separated tokens like: [ID1,ID2]
      // (Some shells strip quotes from the JSON array.)
      const cleaned = tokenIdsRaw.trim().replace(/^\[/, "").replace(/\]$/, "");
      const parts = cleaned
        .split(",")
        .map((s) => s.trim().replace(/^"+|"+$/g, ""))
        .filter(Boolean);
      if (parts.length >= 2) tokenIds = [parts[0]!, parts[1]!];
    }

    if (tokenIds.length < 2) {
      throw new Error(
        "--token-ids must contain 2 token ids (JSON array preferred)",
      );
    }

    const depth = Math.max(1, getIntArg("--depth", 5));
    const intervalMs = Math.max(100, getIntArg("--interval-ms", 1000));

    const book = new OrderBook();
    book.subscribe([String(tokenIds[0]), String(tokenIds[1])]);
    process.stderr.write(
      `book: subscribing tokenIds[0..1], depth=${depth}, intervalMs=${intervalMs}\n`,
    );

    // Readiness: poll snapshot data (avoid relying on internal flags).
    // Note: initial snapshots can sometimes take >10s depending on network/WS.
    // Keep this bounded to fail fast, but allow a bit more room than 10s.
    const readyTimeoutMs = 30_000;
    const readyPollMs = 200;
    const readyStart = Date.now();

    process.stderr.write("book: waiting for initial snapshot...\n");
    while (true) {
      const snap: any = book.getSnapshotData();
      const upOk =
        snap?.up &&
        Array.isArray(snap.up.bids) &&
        Array.isArray(snap.up.asks) &&
        snap.up.bids.length > 0 &&
        snap.up.asks.length > 0;
      const downOk =
        snap?.down &&
        Array.isArray(snap.down.bids) &&
        Array.isArray(snap.down.asks) &&
        snap.down.bids.length > 0 &&
        snap.down.asks.length > 0;

      if (upOk && downOk) break;
      if (Date.now() - readyStart > readyTimeoutMs) {
        throw new Error("timeout waiting for initial orderbook snapshot");
      }
      await sleep(readyPollMs);
    }
    process.stderr.write("book: ready\n");

    const writeSnapshot = () => {
      const snap: any = book.getSnapshotData();
      const clamp = (side: any) => {
        if (!side) return null;
        const bids = Array.isArray(side.bids) ? side.bids.slice(0, depth) : [];
        const asks = Array.isArray(side.asks) ? side.asks.slice(0, depth) : [];
        return { bids, asks };
      };

      const out = {
        ts_ms: Date.now(),
        up: clamp(snap.up),
        down: clamp(snap.down),
      };

      process.stdout.write(JSON.stringify(out));
      process.stdout.write("\n");
    };

    writeSnapshot();
    const timer = setInterval(writeSnapshot, intervalMs);

    const shutdown = () => {
      clearInterval(timer);
      book.destroy();
      process.exit(0);
    };

    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);
  } catch (err: any) {
    const msg = err?.stack || err?.message || String(err);
    process.stderr.write(`ERROR: ${msg}\n`);
    process.exit(1);
  }
}

main();

