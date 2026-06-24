import type { Injection } from "../../Queue/hooks/useScenarios";

export type PlannedInjection = { delayMs: number; text: string };

export function planInjections(injections: Injection[] | undefined): PlannedInjection[] {
  if (!injections || injections.length === 0) return [];
  return injections
    .map((i) => ({ delayMs: Math.max(0, Math.round(i.at_seconds * 1000)), text: i.text }))
    .sort((a, b) => a.delayMs - b.delayMs);
}
