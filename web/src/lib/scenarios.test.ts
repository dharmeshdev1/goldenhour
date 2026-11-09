import { describe, expect, it } from "vitest";
import { cleanScenarios, cleanSeries, interpolateScenario, seriesAt } from "@/lib/scenarios";

const points = cleanScenarios([
  { response_minutes: 60, freezable_saved_usd: 130, drain_prevented_usd: 200 },
  { response_minutes: 0, freezable_saved_usd: 786, drain_prevented_usd: 6000 },
  { response_minutes: 30, freezable_saved_usd: 580, drain_prevented_usd: 2500 },
  { response_minutes: 30, freezable_saved_usd: 600, drain_prevented_usd: 2400 },
  { response_minutes: Number.NaN, freezable_saved_usd: 1, drain_prevented_usd: 1 },
  { response_minutes: 90, freezable_saved_usd: -5, drain_prevented_usd: 100 },
]);

describe("cleanScenarios", () => {
  it("sorts, drops invalid points, keeps the last point per minute and clamps negatives", () => {
    expect(points.map((p) => p.response_minutes)).toEqual([0, 30, 60, 90]);
    expect(points[1]).toEqual({ response_minutes: 30, freezable_saved_usd: 600, drain_prevented_usd: 2400 });
    expect(points[3].freezable_saved_usd).toBe(0);
    expect(cleanScenarios(null)).toEqual([]);
  });
});

describe("interpolateScenario", () => {
  it("is exact at published points", () => {
    expect(interpolateScenario(points, 30)).toEqual({ response_minutes: 30, freezable_saved_usd: 600, drain_prevented_usd: 2400 });
  });
  it("interpolates linearly between points", () => {
    const mid = interpolateScenario(points, 15);
    expect(mid?.freezable_saved_usd).toBeCloseTo(693);
    expect(mid?.drain_prevented_usd).toBeCloseTo(4200);
    const q = interpolateScenario(points, 45);
    expect(q?.freezable_saved_usd).toBeCloseTo(365);
    expect(q?.drain_prevented_usd).toBeCloseTo(1300);
  });
  it("holds the end values outside the published range", () => {
    expect(interpolateScenario(points, -10)?.freezable_saved_usd).toBe(786);
    expect(interpolateScenario(points, 500)?.drain_prevented_usd).toBe(100);
    expect(interpolateScenario(points, 500)?.response_minutes).toBe(500);
  });
  it("returns null without points or for a bad minute", () => {
    expect(interpolateScenario([], 10)).toBeNull();
    expect(interpolateScenario(points, Number.NaN)).toBeNull();
  });
  it("is monotonic when the published points are", () => {
    let previous = Infinity;
    for (let m = 0; m <= 90; m += 0.5) {
      const v = interpolateScenario(points, m)?.drain_prevented_usd ?? 0;
      expect(v).toBeLessThanOrEqual(previous);
      previous = v;
    }
  });
});

describe("series", () => {
  it("reads a minute series between samples", () => {
    expect(seriesAt([0, 10, 20], [0, 100, 300], 15)).toBe(200);
    expect(seriesAt([0, 10, 20], [0, 100, 300], -1)).toBe(0);
    expect(seriesAt([0, 10, 20], [0, 100, 300], 99)).toBe(300);
    expect(seriesAt([], [], 5)).toBe(0);
  });
  it("drops samples that are not finite or out of order", () => {
    const s = cleanSeries({
      minutes: [0, 1, 1, 2, 3],
      drained_usd: [0, 10, 11, 20, Number.NaN],
      freezable_usd: [0, 5, 5, 6, 7],
      escaped_usd: [0, 0, 0, 1, 2],
    });
    expect(s.minutes).toEqual([0, 1, 2]);
    expect(s.drained).toEqual([0, 10, 20]);
  });
});
