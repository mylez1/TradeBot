#!/usr/bin/env bun
/**
 * One-shot market discovery bridge for Python.
 *
 * Outputs a single JSON object to stdout and exits 0 on success.
 * Writes errors to stderr and exits 1 on failure.
 */
process.env["MARKET_ASSET"] = process.env["MARKET_ASSET"] || "btc";
process.env["MARKET_WINDOW"] = process.env["MARKET_WINDOW"] || "5m";

import { APIQueue } from "../tracker/api-queue";
import { getSlotTS, type Slot } from "../utils/slot";
import { Env } from "../utils/config";

const sleep = (ms: number) => new Promise((res) => setTimeout(res, ms));

async function waitFor<T>(
  label: string,
  getValue: () => T | undefined | null,
  timeoutMs: number,
  pollMs: number,
): Promise<T> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const v = getValue();
    if (v !== undefined && v !== null) return v as T;
    await sleep(pollMs);
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function main() {
  try {
    const apiQueue = new APIQueue();

    // Slot (5m/15m) is derived from env via utils/slot + utils/config.
    const slot: Slot = getSlotTS();

    const { slugPrefix, apiSymbol } = Env.getAssetConfig();
    const window = Env.get("MARKET_WINDOW");

    // IMPORTANT: matches scripts/orderbook.ts slug construction.
    const slug = `${slugPrefix}-updown-${window}-${slot.startTime / 1000}`;

    await apiQueue.queueEventDetails(slug);
    apiQueue.queueMarketPrice(slot);

    const event = await waitFor(
      "eventDetails",
      () => apiQueue.eventDetails.get(slug),
      8000,
      200,
    );

    const marketData = await waitFor(
      "marketResult.openPrice",
      () => apiQueue.marketResult.get(slot.startTime),
      15000,
      250,
    );

    const m = event.markets?.[0];
    if (!m) throw new Error("no markets[0] in event details");

    const clobTokenIds: string[] = JSON.parse(m.clobTokenIds);
    const outcomes: string[] = JSON.parse(m.outcomes);

    if (!Array.isArray(clobTokenIds) || clobTokenIds.length < 2) {
      throw new Error("clobTokenIds missing or invalid");
    }

    const result = {
      slug,
      asset: apiSymbol,
      window,
      event_id: event.id,
      neg_risk: event.negRisk,
      market_id: m.id,
      condition_id: m.conditionId,
      clob_token_ids: clobTokenIds,
      outcomes,
      price_to_beat: marketData.openPrice,
      open_time_ms: slot.startTime,
      resolution_time_ms: slot.endTime,
    };

    process.stdout.write(JSON.stringify(result));
    process.stdout.write("\n");
    process.exit(0);
  } catch (err: any) {
    const msg = err?.stack || err?.message || String(err);
    process.stderr.write(`ERROR: ${msg}\n`);
    process.exit(1);
  }
}

main();

