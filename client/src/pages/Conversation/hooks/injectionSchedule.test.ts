import { describe, it, expect } from "vitest";
import { planInjections } from "./injectionSchedule";

describe("planInjections", () => {
  it("converts seconds to ms and sorts ascending", () => {
    const out = planInjections([
      { at_seconds: 3, text: "c" },
      { at_seconds: 1, text: "a" },
      { at_seconds: 2, text: "b" },
    ]);
    expect(out).toEqual([
      { delayMs: 1000, text: "a" },
      { delayMs: 2000, text: "b" },
      { delayMs: 3000, text: "c" },
    ]);
  });

  it("is empty-safe", () => {
    expect(planInjections([])).toEqual([]);
    expect(planInjections(undefined as any)).toEqual([]);
  });
});
