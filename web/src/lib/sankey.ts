import type { FlowGraph, FlowNodeKind } from "@/lib/types";

/**
 * Turns flows.json into something a Sankey layout can draw: valid, merged, acyclic, and with
 * chains too small to see grouped into one band. The layout itself (d3-sankey) only runs in the
 * browser; this module is plain data so it can be tested.
 */

export type Tone = "victim" | "chain" | "held" | "frozen" | "mixer" | "service" | "neutral";

export interface ShapedNode {
  id: string;
  label: string;
  kind: FlowNodeKind;
  tone: Tone;
  /** The original node ids folded into this one (only for the grouped "other chains" node). */
  members: string[];
  /** max(inflow, outflow): the height the node gets in the layout. */
  value: number;
}

export interface ShapedLink {
  source: string;
  target: string;
  usd: number;
}

export interface ShapedFlow {
  nodes: ShapedNode[];
  links: ShapedLink[];
  /** Value leaving the sources (nodes with no inflow): the total the diagram accounts for. */
  total: number;
  /** Value of links that had to be dropped (invalid or closing a cycle). */
  dropped: number;
}

const KINDS: readonly FlowNodeKind[] = ["victim", "chain", "state", "category"];
export const OTHER_CHAINS_ID = "chain:__other";

export function toneFor(node: { id: string; label: string; kind: FlowNodeKind }): Tone {
  if (node.kind === "victim") return "victim";
  if (node.kind === "chain") return "chain";
  const text = `${node.id} ${node.label}`.toLowerCase();
  if (/frozen|freez/.test(text)) return "frozen";
  if (/held|attacker|holding/.test(text)) return "held";
  if (/mix|privacy|coinjoin|tornado/.test(text)) return "mixer";
  if (node.kind === "category" || /exit|exchange|bridge|dex|lending|staking|service|other/.test(text)) return "service";
  return "neutral";
}

function key(a: string, b: string): string {
  return `${a}\u0000${b}`;
}

/** Merge links with the same ends, summing their value. */
function mergeLinks(links: ShapedLink[]): ShapedLink[] {
  const merged = new Map<string, ShapedLink>();
  for (const l of links) {
    const k = key(l.source, l.target);
    const hit = merged.get(k);
    if (hit) hit.usd += l.usd;
    else merged.set(k, { ...l });
  }
  return Array.from(merged.values());
}

/** Keep links largest first, skipping any that would close a cycle. Returns kept and dropped value. */
function breakCycles(links: ShapedLink[]): { kept: ShapedLink[]; dropped: number } {
  const out = new Map<string, Set<string>>();
  const reaches = (from: string, to: string): boolean => {
    const stack = [from];
    const seen = new Set<string>();
    while (stack.length) {
      const n = stack.pop() as string;
      if (n === to) return true;
      if (seen.has(n)) continue;
      seen.add(n);
      for (const next of out.get(n) ?? []) stack.push(next);
    }
    return false;
  };
  const kept: ShapedLink[] = [];
  let dropped = 0;
  for (const l of [...links].sort((a, b) => b.usd - a.usd)) {
    if (reaches(l.target, l.source)) {
      dropped += l.usd;
      continue;
    }
    if (!out.has(l.source)) out.set(l.source, new Set());
    (out.get(l.source) as Set<string>).add(l.target);
    kept.push(l);
  }
  return { kept, dropped };
}

function throughput(links: ShapedLink[]): Map<string, { in: number; out: number }> {
  const t = new Map<string, { in: number; out: number }>();
  const get = (id: string) => {
    let v = t.get(id);
    if (!v) t.set(id, (v = { in: 0, out: 0 }));
    return v;
  };
  for (const l of links) {
    get(l.source).out += l.usd;
    get(l.target).in += l.usd;
  }
  return t;
}

export interface ShapeOptions {
  /** Chains carrying less than this share of the total are grouped. Default 1%. */
  minShare?: number;
  otherLabel?: (count: number) => string;
}

export function shapeFlows(flow: FlowGraph | null | undefined, options: ShapeOptions = {}): ShapedFlow {
  const minShare = options.minShare ?? 0.01;
  const otherLabel = options.otherLabel ?? ((n: number) => `${n} smaller chains`);
  const nodes = new Map<string, { id: string; label: string; kind: FlowNodeKind }>();
  for (const n of Array.isArray(flow?.nodes) ? flow.nodes : []) {
    if (!n || typeof n.id !== "string" || !n.id || nodes.has(n.id)) continue;
    if (!KINDS.includes(n.kind)) continue;
    nodes.set(n.id, { id: n.id, label: typeof n.label === "string" && n.label ? n.label : n.id, kind: n.kind });
  }

  let dropped = 0;
  const valid: ShapedLink[] = [];
  for (const l of Array.isArray(flow?.links) ? flow.links : []) {
    if (!l || typeof l.source !== "string" || typeof l.target !== "string") continue;
    const usd = l.usd;
    if (typeof usd !== "number" || !Number.isFinite(usd) || usd <= 0) continue;
    if (!nodes.has(l.source) || !nodes.has(l.target) || l.source === l.target) {
      dropped += usd;
      continue;
    }
    valid.push({ source: l.source, target: l.target, usd });
  }

  const acyclic = breakCycles(mergeLinks(valid));
  dropped += acyclic.dropped;
  let links = acyclic.kept;

  // The total is what leaves the sources: nodes that receive nothing.
  const flowBefore = throughput(links);
  const total = Array.from(flowBefore.entries())
    .filter(([, v]) => v.in === 0)
    .reduce((s, [, v]) => s + v.out, 0);

  // Group chains too thin to see. One small chain stays itself; grouping it would hide its name.
  const small = Array.from(nodes.values()).filter((n) => {
    if (n.kind !== "chain") return false;
    const v = flowBefore.get(n.id);
    return v !== undefined && Math.max(v.in, v.out) < minShare * total;
  });
  const members: string[] = [];
  if (small.length >= 2) {
    const ids = new Set(small.map((n) => n.id));
    members.push(...small.map((n) => n.id));
    links = mergeLinks(
      links.map((l) => ({
        source: ids.has(l.source) ? OTHER_CHAINS_ID : l.source,
        target: ids.has(l.target) ? OTHER_CHAINS_ID : l.target,
        usd: l.usd,
      })),
    ).filter((l) => l.source !== l.target);
    for (const id of ids) nodes.delete(id);
    nodes.set(OTHER_CHAINS_ID, { id: OTHER_CHAINS_ID, label: otherLabel(small.length), kind: "chain" });
  }

  const flowAfter = throughput(links);
  const shapedNodes: ShapedNode[] = [];
  for (const n of nodes.values()) {
    const v = flowAfter.get(n.id);
    if (!v) continue; // no links: nothing to draw
    shapedNodes.push({
      ...n,
      tone: toneFor(n),
      members: n.id === OTHER_CHAINS_ID ? members : [],
      value: Math.max(v.in, v.out),
    });
  }
  return { nodes: shapedNodes, links, total, dropped };
}
