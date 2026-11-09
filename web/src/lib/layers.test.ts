import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { layerForStage } from "@/lib/layers";
import { normalizeCase, normalizeSummary } from "@/lib/normalize";

const kase = normalizeCase(JSON.parse(readFileSync(new URL("../../fixtures/api/v1/case.json", import.meta.url), "utf8")));

describe("layerForStage", () => {
  it("maps each stage of the Binance timeline to the layer that answers it", () => {
    const layers = kase.timeline.map((t) => layerForStage(t.what, "during").layer);
    expect(layers).toEqual(["guard", "guard", "guard", "guard", "watch", "trace", "respond", "watch", "guard", "screen"]);
  });
  it("marks the signing service stage as the breakpoint, and only that one", () => {
    const marked = kase.timeline.filter((t) => layerForStage(t.what, "during").breakpoint);
    expect(marked).toHaveLength(1);
    expect(marked[0].what).toMatch(/signing service/);
  });
  it("falls back by phase for wording it does not recognize", () => {
    expect(layerForStage("Something new happened", "before").layer).toBe("guard");
    expect(layerForStage("Something new happened", "during").layer).toBe("watch");
    expect(layerForStage("Something new happened", "after").layer).toBe("trace");
  });
});

describe("normalize", () => {
  it("reads a sparse summary without crashing", () => {
    const s = normalizeSummary({ stolen: { usd_at_theft: 5, by_chain: [{ chain: "dogecoin", usd: 1 }, { chain: "bitcoin", usd: 4 }] } });
    expect(s.stolen.usd_at_theft).toBe(5);
    expect(s.stolen.by_chain).toEqual([{ chain: "bitcoin", usd: 4 }]);
    expect(s.now.held.by_chain).toEqual([]);
    expect(s.last_movement_at).toBeNull();
    expect(s.stolen.reported_usd).toBeUndefined();
  });
  it("keeps the reported total only when it is a positive number", () => {
    expect(normalizeSummary({ stolen: { reported_usd: 6.43e9 } }).stolen.reported_usd).toBe(6.43e9);
    expect(normalizeSummary({ stolen: { reported_usd: "6.43e9" } }).stolen.reported_usd).toBeUndefined();
    expect(normalizeSummary({ stolen: { reported_usd: 0 } }).stolen.reported_usd).toBeUndefined();
  });
  it("keeps the case timeline and sources", () => {
    expect(kase.timeline).toHaveLength(10);
    expect(kase.sources.map((s) => s.id)).toContain("elliptic");
  });
});
