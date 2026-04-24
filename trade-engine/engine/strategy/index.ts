import type { Strategy } from "./types.ts";
import { simulationStrategy } from "./simulation.ts";
import { lateEntry } from "./late-entry.ts";

export const strategies: Record<string, Strategy> = {
  "simulation": simulationStrategy,
  "late-entry": lateEntry,
};

export const DEFAULT_STRATEGY = "simulation";

export type { Strategy, StrategyContext } from "./types.ts";
