import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { OTHER_CHAINS_ID, shapeFlows, toneFor } from "@/lib/sankey";
import type { FlowGraph } from "@/lib/types";

const base: FlowGraph = {
  nodes: [
    { id: "victim", label: "Binance", kind: "victim" },
    { id: "chain:ethereum", label: "Ethereum", kind: "chain" },
    { id: "chain:bitcoin", label: "Bitcoin", kind: "chain" },
    { id: "chain:near", label: "NEAR", kind: "chain" },
    { id: "chain:polygon", label: "Polygon", kind: "chain" },
    { id: "state:held", label: "Held by attacker addresses", kind: "state" },
    { id: "state:frozen", label: "Frozen by issuers", kind: "state" },
    { id: "exit:mixer", label: "Mixers and privacy pools", kind: "category" },
  ],
  links: [
    { source: "victim", target: "chain:ethereum", usd: 600 },
    { source: "victim", target: "chain:bitcoin", usd: 396 },
    { source: "victim", target: "chain:near", usd: 2 },
    { source: "victim", target: "chain:polygon", usd: 2 },
    { source: "chain:ethereum", target: "state:frozen", usd: 400 },
    { source: "chain:ethereum", target: "state:held", usd: 150 },
    { source: "chain:ethereum", target: "state:held", usd: 50 },
    { source: "chain:bitcoin", target: "state:held", usd: 300 },
    { source: "chain:bitcoin", target: "exit:mixer", usd: 96 },
    { source: "chain:near", target: "state:held", usd: 2 },
    { source: "chain:polygon", target: "state:held", usd: 2 },
  ],
};

describe("shapeFlows", () => {
  it("merges duplicate links and keeps the total", () => {
    const s = shapeFlows(base, { minShare: 0 });
    const held = s.links.filter((l) => l.source === "chain:ethereum" && l.target === "state:held");
    expect(held).toHaveLength(1);
    expect(held[0].usd).toBe(200);
    expect(s.total).toBe(1000);
    expect(s.dropped).toBe(0);
  });

  it("groups chains under the minimum share into one band", () => {
    const s = shapeFlows(base, { minShare: 0.01 });
    const other = s.nodes.find((n) => n.id === OTHER_CHAINS_ID);
    expect(other?.members.sort()).toEqual(["chain:near", "chain:polygon"]);
    expect(other?.value).toBe(4);
    expect(s.links.find((l) => l.source === "victim" && l.target === OTHER_CHAINS_ID)?.usd).toBe(4);
    expect(s.links.find((l) => l.source === OTHER_CHAINS_ID && l.target === "state:held")?.usd).toBe(4);
    expect(s.nodes.some((n) => n.id === "chain:near")).toBe(false);
  });

  it("leaves a single small chain alone", () => {
    const one: FlowGraph = { nodes: base.nodes, links: base.links.filter((l) => !l.source.includes("polygon") && !l.target.includes("polygon")) };
    const s = shapeFlows(one, { minShare: 0.01 });
    expect(s.nodes.some((n) => n.id === "chain:near")).toBe(true);
    expect(s.nodes.some((n) => n.id === OTHER_CHAINS_ID)).toBe(false);
  });

  it("drops invalid links and counts what it drops", () => {
    const s = shapeFlows({
      nodes: base.nodes,
      links: [
        ...base.links,
        { source: "victim", target: "nowhere", usd: 7 },
        { source: "chain:bitcoin", target: "chain:bitcoin", usd: 3 },
        { source: "victim", target: "chain:bitcoin", usd: -5 },
        { source: "victim", target: "chain:bitcoin", usd: Number.NaN },
      ],
    });
    expect(s.dropped).toBe(10);
    expect(s.total).toBe(1000);
  });

  it("breaks cycles by dropping the smallest link that closes one", () => {
    const s = shapeFlows({
      nodes: base.nodes,
      links: [...base.links, { source: "state:held", target: "chain:ethereum", usd: 5 }],
    });
    expect(s.links.some((l) => l.source === "state:held")).toBe(false);
    expect(s.dropped).toBe(5);
  });

  it("removes nodes without links and survives empty input", () => {
    const s = shapeFlows({ nodes: [...base.nodes, { id: "exit:dex", label: "DEX", kind: "category" }], links: base.links });
    expect(s.nodes.some((n) => n.id === "exit:dex")).toBe(false);
    expect(shapeFlows(null)).toEqual({ nodes: [], links: [], total: 0, dropped: 0 });
  });

  it("assigns tones from kind and wording", () => {
    expect(toneFor({ id: "state:held", label: "Held by attacker addresses", kind: "state" })).toBe("held");
    expect(toneFor({ id: "state:frozen", label: "Frozen by issuers", kind: "state" })).toBe("frozen");
    expect(toneFor({ id: "exit:mixer", label: "Mixers and privacy pools", kind: "category" })).toBe("mixer");
    expect(toneFor({ id: "exit:exchange", label: "Exchanges", kind: "category" })).toBe("service");
    expect(toneFor({ id: "chain:tron", label: "Tron", kind: "chain" })).toBe("chain");
  });

  it("conserves value through every chain in the development fixture", () => {
    const flow = JSON.parse(readFileSync(new URL("../../fixtures/api/v1/flows.json", import.meta.url), "utf8")) as FlowGraph;
    const s = shapeFlows(flow);
    expect(s.dropped).toBe(0);
    for (const n of s.nodes.filter((x) => x.kind === "chain")) {
      const inflow = s.links.filter((l) => l.target === n.id).reduce((a, l) => a + l.usd, 0);
      const outflow = s.links.filter((l) => l.source === n.id).reduce((a, l) => a + l.usd, 0);
      expect(Math.abs(inflow - outflow)).toBeLessThan(inflow * 1e-6 + 2);
    }
  });
});
