import { chainColor } from "@/lib/chains";
import type { NodeRole, TrailEdge, TrailNode } from "@/lib/types";
import { COLOR, EXIT_COLOR } from "@/lib/tones";

export type ColorBy = "role" | "category" | "chain";
export type SizeBy = "received" | "held";

export const ROLE_INFO: Record<NodeRole, { label: string; color: string; plural: string }> = {
  victim: { label: "Victim wallet", plural: "Victim wallets", color: COLOR.gold },
  seed: { label: "Seed (named)", plural: "Seeds", color: COLOR.red },
  trail: { label: "On the trail", plural: "Trail addresses", color: COLOR.ember },
  hub: { label: "Bitcoin transaction", plural: "Bitcoin transactions", color: "#7d7366" },
  service: { label: "Service", plural: "Services", color: COLOR.slate },
  counterparty: { label: "Counterparty", plural: "Counterparties", color: "#d9cbb4" },
};

export const ROLES: NodeRole[] = ["victim", "seed", "trail", "hub", "service", "counterparty"];

const CATEGORY_COLOR: Record<string, string> = {
  ...EXIT_COLOR,
  attacker: COLOR.ember,
  custody: COLOR.gold,
  transaction: "#7d7366",
  contract: COLOR.red,
  unknown: "#d9cbb4",
};

export function categoryColor(category: string): string {
  return CATEGORY_COLOR[category] ?? "#8d8478";
}

/** The value that sizes a node: what it received, or for a victim what left it. */
export function nodeValue(n: TrailNode, by: SizeBy): number {
  return by === "held" ? n.held_usd : n.received_usd;
}

export function colorFor(n: TrailNode, by: ColorBy): string {
  if (by === "chain") return chainColor(n.chain);
  if (by === "category") return categoryColor(n.category);
  if (n.role === "service") return categoryColor(n.category);
  return ROLE_INFO[n.role]?.color ?? "#8d8478";
}

export function sizeFor(n: TrailNode, by: SizeBy, max: number): number {
  const v = Math.max(0, nodeValue(n, by));
  // Victim wallets sit at the center; keep them from swallowing the first rings.
  const scale = n.role === "victim" ? 5 : 10;
  return 1.8 + scale * Math.sqrt(v / Math.max(1, max));
}

export function edgeColorFor(e: TrailEdge, max: number): string {
  if (e.kind === "bridge") return "rgba(242,179,61,0.55)";
  const a = 0.06 + 0.22 * Math.sqrt(e.usd / Math.max(1, max));
  return `rgba(255,214,170,${a.toFixed(3)})`;
}

export function edgeSizeFor(e: TrailEdge, max: number): number {
  return 0.4 + 3.2 * Math.sqrt(e.usd / Math.max(1, max));
}

/** Minimum value filter steps, in dollars. */
export const MIN_STEPS = [0, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000];
