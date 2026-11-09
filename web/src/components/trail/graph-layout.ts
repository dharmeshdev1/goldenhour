import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceRadial,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationNodeDatum,
} from "d3-force";
import type { TrailEdge, TrailNode } from "@/lib/types";

/**
 * The trail layout, shared by the WebGL renderer and its Canvas fallback. "Rings" pulls each
 * address to the ring of its hop from the victim; "force" lets the structure find its own shape.
 * Units are roughly screen pixels at the default zoom.
 */

export interface SimNode extends SimulationNodeDatum {
  id: string;
  hop: number;
  r: number;
  service: boolean;
}

/** Ring radii that fit the data: a crowded hop gets a ring long enough to hold its addresses. */
export function ringRadii(nodes: TrailNode[], sizeOf: (n: TrailNode) => number): number[] {
  const maxHop = Math.max(0, ...nodes.map((n) => n.hop));
  const need = Array.from({ length: maxHop + 1 }, () => 0);
  for (const n of nodes) need[Math.max(0, n.hop)] += 2 * sizeOf(n) + 3;
  const radii: number[] = [];
  for (let h = 0; h <= maxHop; h++) {
    const fit = need[h] / (2 * Math.PI);
    radii.push(h === 0 ? Math.max(14, fit * 0.6) : Math.max(radii[h - 1] + 20, fit));
  }
  return radii;
}

export function radiusFor(radii: number[], n: TrailNode): number {
  const h = Math.min(Math.max(0, n.hop), radii.length - 1);
  if (n.role !== "service") return radii[h];
  // Services sit between their hop's ring and the next one.
  return h + 1 < radii.length ? (radii[h] + radii[h + 1]) / 2 : radii[h] + 14;
}

/** Starting positions: each address on its hop ring, inside its chain's sector. */
export function initialPositions(nodes: TrailNode[], radii: number[]): Map<string, { x: number; y: number }> {
  const chains = Array.from(new Set(nodes.map((n) => n.chain)));
  const perChain = new Map(chains.map((c) => [c, nodes.filter((n) => n.chain === c).length]));
  const sector = new Map<string, [number, number]>();
  let cursor = 0;
  for (const c of chains) {
    const span = (2 * Math.PI * (perChain.get(c) as number)) / Math.max(1, nodes.length);
    sector.set(c, [cursor, span]);
    cursor += span;
  }
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const out = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const [start, span] = sector.get(n.chain) as [number, number];
    const angle = start + rand() * span;
    const r = radiusFor(radii, n);
    out.set(n.id, { x: Math.cos(angle) * r, y: Math.sin(angle) * r });
  }
  return out;
}

export function makeSimulation(
  sim: SimNode[],
  nodes: TrailNode[],
  edges: TrailEdge[],
  layout: "rings" | "force",
  radii: number[],
): Simulation<SimNode, undefined> {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const ids = new Set(sim.map((s) => s.id));
  const links = edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ source: e.source, target: e.target }));
  const simulation = forceSimulation<SimNode>(sim)
    .force("collide", forceCollide<SimNode>((d) => d.r).strength(0.85))
    .force("charge", forceManyBody<SimNode>().strength(layout === "rings" ? -3 : -16).distanceMax(layout === "rings" ? 120 : 260))
    .force(
      "link",
      forceLink<SimNode, { source: string | SimNode; target: string | SimNode }>(links)
        .id((d) => d.id)
        .distance(layout === "rings" ? 24 : 22)
        .strength(layout === "rings" ? 0.04 : 0.4),
    )
    .alphaDecay(0.03);
  if (layout === "rings") {
    simulation.force("radial", forceRadial<SimNode>((d) => radiusFor(radii, byId.get(d.id) as TrailNode), 0, 0).strength(0.85));
  } else {
    simulation.force("x", forceX<SimNode>(0).strength(0.02)).force("y", forceY<SimNode>(0).strength(0.02));
  }
  return simulation;
}
