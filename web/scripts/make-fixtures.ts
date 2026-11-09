// Development fixtures for every endpoint of api/v1, from one seeded simulation of the
// 29 October 2026 Binance theft, so the documents agree with each other: the Sankey, the summary,
// the graph, the feed, the Parquet files and the screening shards all describe the same trail.
//
// The magnitudes follow the public facts (the case file and its sources). Addresses and
// transaction hashes are random and belong to nobody. These files are for `pnpm dev` and tests
// only; they are never part of a build (see scripts/fixtures.mjs).
//
// Run: node scripts/make-fixtures.ts   (Parquet needs `uv`; without it the JSON is still written)

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type {
  CaseFile,
  Chain,
  Detector,
  ExitCategory,
  Feed,
  FeedItem,
  FlowGraph,
  GoldenHour,
  GuardCases,
  Meta,
  ScreenEntry,
  Summary,
  TrailEdge,
  TrailGraph,
  TrailNode,
} from "../src/lib/types.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(root, "fixtures", "api", "v1");

// ---------------------------------------------------------------------------------------------
// Randomness, deterministic

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const rnd = mulberry32(20261029);
const between = (a: number, b: number) => a + (b - a) * rnd();
const intBetween = (a: number, b: number) => Math.floor(between(a, b + 1));
const pick = <T>(xs: readonly T[]): T => xs[Math.floor(rnd() * xs.length)];
const expo = (mean: number) => -Math.log(1 - rnd()) * mean;
const round = (v: number, d = 2) => Math.round(v * 10 ** d) / 10 ** d;

function weightedPick<T>(xs: readonly T[], weight: (x: T) => number): T {
  const ws = xs.map(weight);
  const total = ws.reduce((s, w) => s + w, 0);
  let r = rnd() * total;
  for (let i = 0; i < xs.length; i++) {
    r -= ws[i];
    if (r <= 0) return xs[i];
  }
  return xs[xs.length - 1];
}

/** Split a total into n random positive parts; skew > 1 makes a few parts dominate. */
function split(total: number, n: number, skew = 1.6): number[] {
  const ws = Array.from({ length: n }, () => Math.pow(rnd(), skew) + 0.03);
  const sum = ws.reduce((s, w) => s + w, 0);
  return ws.map((w) => (total * w) / sum);
}

// ---------------------------------------------------------------------------------------------
// Addresses and hashes (random, valid in format, owned by nobody)

const HEX = "0123456789abcdef";
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const BECH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const EVM: Chain[] = ["ethereum", "optimism", "arbitrum", "base", "polygon", "avalanche"];

const chars = (alphabet: string, n: number) => {
  let s = "";
  for (let i = 0; i < n; i++) s += alphabet[Math.floor(rnd() * alphabet.length)];
  return s;
};

const used = new Set<string>();
function address(chain: Chain): string {
  for (;;) {
    let a: string;
    if (EVM.includes(chain)) a = `0x${chars(HEX, 40)}`;
    else if (chain === "bitcoin") {
      const r = rnd();
      a = r < 0.72 ? `bc1q${chars(BECH, 38)}` : r < 0.86 ? `bc1p${chars(BECH, 58)}` : `3${chars(B58, 33)}`;
    } else if (chain === "tron") a = `T${chars(B58, 33)}`;
    else if (chain === "xrpl") a = `r${chars(B58, intBetween(24, 33))}`;
    else if (chain === "solana") a = chars(B58, intBetween(43, 44));
    else a = chars(HEX, 64); // near implicit account
    if (!used.has(a)) {
      used.add(a);
      return a;
    }
  }
}

function txHash(chain: Chain): string {
  if (EVM.includes(chain)) return `0x${chars(HEX, 64)}`;
  if (chain === "xrpl") return chars(HEX, 64).toUpperCase();
  if (chain === "solana") return chars(B58, 88);
  if (chain === "near") return chars(B58, 44);
  return chars(HEX, 64); // bitcoin, tron
}

const sha256 = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const EXPLORER: Record<Chain, (tx: string) => string> = {
  ethereum: (h) => `https://etherscan.io/tx/${h}`,
  optimism: (h) => `https://optimistic.etherscan.io/tx/${h}`,
  arbitrum: (h) => `https://arbiscan.io/tx/${h}`,
  base: (h) => `https://basescan.org/tx/${h}`,
  polygon: (h) => `https://polygonscan.com/tx/${h}`,
  avalanche: (h) => `https://snowtrace.io/tx/${h}`,
  bitcoin: (h) => `https://mempool.space/tx/${h}`,
  tron: (h) => `https://tronscan.org/#/transaction/${h}`,
  xrpl: (h) => `https://xrpscan.com/tx/${h}`,
  solana: (h) => `https://solscan.io/tx/${h}`,
  near: (h) => `https://nearblocks.io/txns/${h}`,
};

// ---------------------------------------------------------------------------------------------
// Time

const T0 = Date.parse("2026-10-29T20:05:00Z");
const FIRST_THEFT = Date.parse("2026-10-29T20:05:04Z");
const LAST_THEFT = Date.parse("2026-10-29T23:56:12Z");
const DATA_THROUGH = Date.parse("2026-11-07T08:50:00Z");
const GENERATED = Date.parse("2026-11-07T08:55:00Z");
const iso = (ms: number) => new Date(ms).toISOString().replace(".000Z", "Z");
const atMinute = (m: number) => T0 + m * 60_000;
const sec = (ms: number) => Math.floor(ms / 1000);
const H = 3_600_000;

// ---------------------------------------------------------------------------------------------
// The theft, in numbers consistent with the case file

const CHAINS: Chain[] = ["ethereum", "bitcoin", "xrpl", "tron", "solana", "base", "arbitrum", "optimism", "polygon", "avalanche", "near"];
const NATIVE: Record<Chain, string> = {
  ethereum: "ETH",
  optimism: "ETH",
  arbitrum: "ETH",
  base: "ETH",
  polygon: "POL",
  avalanche: "AVAX",
  bitcoin: "BTC",
  tron: "TRX",
  xrpl: "XRP",
  solana: "SOL",
  near: "NEAR",
};
const PRICE_THEFT: Record<string, number> = { BTC: 94_500, ETH: 3_420, XRP: 2.1, TRX: 0.3, SOL: 185, AVAX: 26, POL: 0.42, NEAR: 3.1 };
const PRICE_NOW: Record<string, number> = { BTC: 89_800, ETH: 3_250, XRP: 2.02, TRX: 0.29, SOL: 176, AVAX: 24.8, POL: 0.39, NEAR: 2.9 };
const STABLE = new Set(["USDT", "USDC", "FDUSD", "USDP", "PYUSD", "RLUSD"]);
const priceAtTheft = (sym: string) => (STABLE.has(sym) ? 1 : PRICE_THEFT[sym]);
const nowRatio = (chain: Chain) => PRICE_NOW[NATIVE[chain]] / PRICE_THEFT[NATIVE[chain]];

/** Drain tranches: what left the victim's wallets, when (minutes after 20:05 UTC). */
interface Tranche {
  chain: Chain;
  symbol: string;
  amount: number;
  from: number;
  to: number;
}
const TRANCHES: Tranche[] = [
  { chain: "xrpl", symbol: "XRP", amount: 233_460_000, from: 0, to: 4 },
  { chain: "xrpl", symbol: "RLUSD", amount: 209_000_000, from: 5, to: 9 },
  { chain: "ethereum", symbol: "ETH", amount: 300_000_000 / 3_420, from: 15, to: 60 },
  { chain: "ethereum", symbol: "USDT", amount: 1_180_000_000, from: 18, to: 24 },
  { chain: "ethereum", symbol: "USDC", amount: 640_000_000, from: 20, to: 34 },
  { chain: "ethereum", symbol: "FDUSD", amount: 512_000_000, from: 30, to: 48 },
  { chain: "ethereum", symbol: "USDP", amount: 120_000_000, from: 44, to: 60 },
  { chain: "ethereum", symbol: "ETH", amount: 122_600 - 300_000_000 / 3_420 - 2_961.9, from: 60, to: 229 },
  { chain: "ethereum", symbol: "ETH", amount: 2_961.9, from: 231.2, to: 231.2 },
  { chain: "bitcoin", symbol: "BTC", amount: 18_270, from: 15, to: 45 },
  { chain: "tron", symbol: "TRX", amount: 420_000_000, from: 22, to: 80 },
  { chain: "tron", symbol: "USDT", amount: 620_000_000, from: 24, to: 40 },
  { chain: "solana", symbol: "SOL", amount: 180_000, from: 35, to: 90 },
  { chain: "solana", symbol: "USDT", amount: 52_000_000, from: 36, to: 44 },
  { chain: "solana", symbol: "PYUSD", amount: 62_000_000, from: 38, to: 50 },
  { chain: "base", symbol: "USDC", amount: 103_000_000, from: 44, to: 50 },
  { chain: "base", symbol: "ETH", amount: 1_500, from: 48, to: 120 },
  { chain: "arbitrum", symbol: "USDC", amount: 80_000_000, from: 50, to: 56 },
  { chain: "arbitrum", symbol: "ETH", amount: 2_400, from: 52, to: 130 },
  { chain: "optimism", symbol: "ETH", amount: 1_900, from: 60, to: 140 },
  { chain: "polygon", symbol: "POL", amount: 12_000_000, from: 70, to: 150 },
  { chain: "avalanche", symbol: "AVAX", amount: 400_000, from: 65, to: 160 },
  { chain: "near", symbol: "NEAR", amount: 1_100_000, from: 110, to: 200 },
];
const trancheUsd = (t: Tranche) => t.amount * priceAtTheft(t.symbol);

/** Issuer freezes. `minute` is when the freeze landed, after 20:05 UTC. */
interface Freeze {
  issuer: string;
  chain: Chain;
  symbol: string;
  amount: number;
  addresses: number;
  minute: number;
}
const FREEZES: Freeze[] = [
  { issuer: "Circle", chain: "ethereum", symbol: "USDC", amount: 640_000_000, addresses: 11, minute: 46 },
  { issuer: "Circle", chain: "base", symbol: "USDC", amount: 103_000_000, addresses: 2, minute: 53 },
  { issuer: "Tether", chain: "ethereum", symbol: "USDT", amount: 746_000_000, addresses: 14, minute: 57 },
  { issuer: "Circle", chain: "arbitrum", symbol: "USDC", amount: 80_000_000, addresses: 2, minute: 60 },
  { issuer: "Ripple", chain: "xrpl", symbol: "RLUSD", amount: 177_000_000, addresses: 2, minute: 75 },
  { issuer: "Tether", chain: "tron", symbol: "USDT", amount: 352_000_000, addresses: 7, minute: 95 },
  { issuer: "First Digital", chain: "ethereum", symbol: "FDUSD", amount: 512_000_000, addresses: 6, minute: 145 },
  { issuer: "Paxos", chain: "ethereum", symbol: "USDP", amount: 120_000_000, addresses: 3, minute: 310 },
  { issuer: "Paxos", chain: "solana", symbol: "PYUSD", amount: 62_000_000, addresses: 2, minute: 395 },
];
// A later freeze: USDT bought back on Tron for an over-the-counter sale, frozen at a trail address.
const LATE_FREEZE = { issuer: "Tether", chain: "tron" as Chain, symbol: "USDT", amount: 3_100_000, at: Date.parse("2026-11-06T14:22:00Z") };

/** Stablecoins swapped before any freeze reached them: the value that escaped. */
const SWAPS: { chain: Chain; symbol: string; amount: number; from: number; to: number; into: string; venue: string }[] = [
  { chain: "ethereum", symbol: "USDT", amount: 434_000_000, from: 22, to: 56, into: "ETH", venue: "Uniswap" },
  { chain: "tron", symbol: "USDT", amount: 268_000_000, from: 30, to: 94, into: "TRX", venue: "SunSwap" },
  { chain: "solana", symbol: "USDT", amount: 52_000_000, from: 40, to: 70, into: "SOL", venue: "Jupiter" },
  { chain: "xrpl", symbol: "RLUSD", amount: 32_000_000, from: 9, to: 60, into: "XRP", venue: "XRPL AMM" },
];

/** Where the non-frozen value left the attacker's direct control, per chain of theft (USD). */
const EXITS: Record<Chain, Partial<Record<ExitCategory, number>>> = {
  bitcoin: { exchange: 140e6, mixer: 222e6, bridge: 74.515e6 },
  ethereum: { exchange: 66e6, mixer: 166e6, bridge: 131e6, dex: 40e6, lending: 38e6, staking: 22e6 },
  xrpl: { exchange: 60e6, bridge: 104e6, dex: 18e6 },
  tron: { exchange: 30e6, bridge: 140e6, dex: 6e6, other: 22e6 },
  solana: { bridge: 32e6, dex: 4e6 },
  avalanche: { bridge: 1.5e6 },
  base: {},
  arbitrum: {},
  optimism: {},
  polygon: {},
  near: {},
};

const ENTITIES: Record<ExitCategory, Partial<Record<Chain, [string, number][]>>> = {
  exchange: {
    bitcoin: [["Instant exchange A", 0.6], ["OTC desk C", 0.25], ["Exchange D", 0.15]],
    ethereum: [["Instant exchange A", 0.5], ["Instant exchange B", 0.5]],
    xrpl: [["Exchange D", 0.7], ["Instant exchange B", 0.3]],
    tron: [["Instant exchange B", 1]],
  },
  mixer: {
    bitcoin: [["Wasabi coinjoin", 0.74], ["JoinMarket", 0.26]],
    ethereum: [["Railgun", 0.8], ["Privacy Pools", 0.2]],
  },
  bridge: {
    bitcoin: [["THORChain", 1]],
    ethereum: [["THORChain", 0.6], ["Wormhole", 0.25], ["deBridge", 0.15]],
    xrpl: [["THORChain", 1]],
    tron: [["Allbridge", 1]],
    solana: [["Wormhole", 0.7], ["deBridge", 0.3]],
    avalanche: [["Stargate", 1]],
  },
  dex: {
    ethereum: [["Uniswap", 0.65], ["Curve", 0.35]],
    xrpl: [["XRPL AMM", 1]],
    tron: [["SunSwap", 1]],
    solana: [["Jupiter", 1]],
  },
  lending: { ethereum: [["Aave", 0.68], ["Compound", 0.32]] },
  staking: { ethereum: [["Lido", 1]] },
  other: { tron: [["Unlabeled contracts", 1]] },
};

const SERVICE_LABEL: Record<string, string> = {
  THORChain: "THORChain vault",
  "Wasabi coinjoin": "Wasabi coinjoin coordinator",
  JoinMarket: "JoinMarket maker pool",
  Railgun: "Railgun relay adapt",
  "Privacy Pools": "Privacy Pools entrypoint",
  Wormhole: "Wormhole token bridge",
  deBridge: "deBridge gate",
  Allbridge: "Allbridge core pool",
  Stargate: "Stargate router",
  Uniswap: "Uniswap universal router",
  Curve: "Curve 3pool",
  "XRPL AMM": "XRPL AMM pool XRP/RLUSD",
  SunSwap: "SunSwap router",
  Jupiter: "Jupiter aggregator",
  Aave: "Aave V3 pool",
  Compound: "Compound III market",
  Lido: "Lido stETH",
  "Unlabeled contracts": "Unlabeled contract",
};

const CONVERTED_TO_BTC = { btc: 3_284.6, usd: 310_400_000, swaps: 1_184 };

/** Graph shape per chain: how many of the top nodes the graph shows. */
const SPEC: Record<Chain, { victims: number; seeds: number; trail: number; maxHop: number; delayH: number; visible: number }> = {
  bitcoin: { victims: 4, seeds: 19, trail: 118, maxHop: 14, delayH: 9, visible: 0.93 },
  ethereum: { victims: 8, seeds: 34, trail: 96, maxHop: 9, delayH: 6, visible: 0.9 },
  tron: { victims: 3, seeds: 10, trail: 62, maxHop: 7, delayH: 3, visible: 0.45 },
  xrpl: { victims: 5, seeds: 6, trail: 22, maxHop: 6, delayH: 7, visible: 0.9 },
  solana: { victims: 2, seeds: 5, trail: 12, maxHop: 5, delayH: 8, visible: 0.9 },
  base: { victims: 1, seeds: 2, trail: 4, maxHop: 4, delayH: 10, visible: 0.95 },
  arbitrum: { victims: 1, seeds: 2, trail: 4, maxHop: 4, delayH: 10, visible: 0.95 },
  optimism: { victims: 1, seeds: 1, trail: 3, maxHop: 4, delayH: 10, visible: 0.95 },
  polygon: { victims: 1, seeds: 1, trail: 3, maxHop: 4, delayH: 10, visible: 0.95 },
  avalanche: { victims: 1, seeds: 2, trail: 3, maxHop: 4, delayH: 10, visible: 0.95 },
  near: { victims: 1, seeds: 1, trail: 2, maxHop: 3, delayH: 12, visible: 0.95 },
};

// ---------------------------------------------------------------------------------------------
// Per-chain accounting, at theft prices

const stolenByChain = new Map<Chain, number>();
for (const t of TRANCHES) stolenByChain.set(t.chain, (stolenByChain.get(t.chain) ?? 0) + trancheUsd(t));
const frozenByChain = new Map<Chain, number>();
for (const f of FREEZES) frozenByChain.set(f.chain, (frozenByChain.get(f.chain) ?? 0) + f.amount);
frozenByChain.set("tron", (frozenByChain.get("tron") ?? 0) + LATE_FREEZE.amount);
const exitsTotal = (c: Chain) => Object.values(EXITS[c]).reduce((s, v) => s + (v ?? 0), 0);
const heldTheftByChain = new Map<Chain, number>();
for (const c of CHAINS) {
  const held = (stolenByChain.get(c) ?? 0) - (frozenByChain.get(c) ?? 0) - exitsTotal(c);
  if (held < 0) throw new Error(`negative held value on ${c}`);
  heldTheftByChain.set(c, held);
}
const STOLEN_TOTAL = Array.from(stolenByChain.values()).reduce((s, v) => s + v, 0);

// ---------------------------------------------------------------------------------------------
// The graph

interface GNode extends TrailNode {
  parents: string[];
  kids: string[];
  free: number; // attacker-controlled value that passed through, at theft prices
  frozen: number;
  heldTheft: number;
  outFixed: number; // value sent to services and counterparties
  stable?: string;
}

const nodes = new Map<string, GNode>();
const edges = new Map<string, TrailEdge & { theft?: boolean; viaHubInput?: string }>();
const edgeKey = (s: string, t: string) => `${s}>${t}`;

function addNode(chain: Chain, role: TrailNode["role"], props: Partial<GNode> = {}): GNode {
  const isTx = role === "hub";
  const addr = isTx ? txHash("bitcoin") : address(chain);
  const id = isTx ? `bitcoin:@tx:${addr}` : `${chain}:${addr}`;
  const n: GNode = {
    id,
    chain,
    address: addr,
    role,
    category: "",
    label: "",
    hop: 0,
    received_usd: 0,
    held_usd: 0,
    first_ts: 0,
    parents: [],
    kids: [],
    free: 0,
    frozen: 0,
    heldTheft: 0,
    outFixed: 0,
    ...props,
  };
  nodes.set(id, n);
  return n;
}

function link(a: GNode, b: GNode, kind: TrailEdge["kind"] = "transfer") {
  const k = edgeKey(a.id, b.id);
  if (!edges.has(k)) edges.set(k, { source: a.id, target: b.id, usd: 0, count: 1, first_ts: 0, last_ts: 0, kind });
  a.kids.push(b.id);
  b.parents.push(a.id);
}

const fbiNumbers = Array.from({ length: 628 }, (_, i) => i + 1).sort(() => rnd() - 0.5);
let fbiNext = 0;
const victimsByChain = new Map<Chain, GNode[]>();
const seedsByChain = new Map<Chain, GNode[]>();
let victimCounter = 0;

for (const chain of CHAINS) {
  const spec = SPEC[chain];
  const victims = Array.from({ length: spec.victims }, () =>
    addNode(chain, "victim", { category: "custody", label: `Binance hot wallet ${++victimCounter}`, hop: 0 }),
  );
  victimsByChain.set(chain, victims);
  const seeds = Array.from({ length: spec.seeds }, () =>
    addNode(chain, "seed", { category: "attacker", label: `FBI list #${fbiNumbers[fbiNext++]}`, hop: 1 }),
  );
  if (chain === "ethereum") {
    seeds[0].label = "EIP-7702 delegate contract";
    seeds[0].category = "contract";
  }
  seedsByChain.set(chain, seeds);
  for (const s of seeds) {
    link(pick(victims), s);
    if (victims.length > 1 && rnd() < 0.35) {
      const other = pick(victims.filter((v) => !s.parents.includes(v.id)));
      link(other, s);
    }
  }
}

// Trail trees under the seeds. Bitcoin spends through transaction nodes.
const hubOf = new Map<string, GNode[]>();
for (const chain of CHAINS) {
  const spec = SPEC[chain];
  const seeds = seedsByChain.get(chain) as GNode[];
  const pool: GNode[] = [...seeds];
  let remaining = spec.trail;
  if (chain === "tron") {
    // One seed fans TRX out to many fresh addresses in a few minutes.
    const fan = seeds[1];
    fan.label = `${fan.label} (fan-out)`;
    for (let i = 0; i < 38; i++) {
      const child = addNode(chain, "trail", { category: "attacker", hop: 2 });
      link(fan, child);
      pool.push(child);
    }
    remaining -= 38;
  }
  for (let i = 0; i < remaining; i++) {
    const parent = weightedPick(
      pool.filter((p) => p.hop + (chain === "bitcoin" ? 2 : 1) <= spec.maxHop),
      (p) => (p.role === "seed" ? 1.4 : 1) / Math.pow(1 + p.kids.length, 1.3),
    );
    if (chain === "bitcoin") {
      const hubs = hubOf.get(parent.id) ?? [];
      let hub: GNode;
      if (hubs.length && rnd() < 0.55) hub = pick(hubs);
      else {
        hub = addNode("bitcoin", "hub", { category: "transaction", hop: parent.hop + 1 });
        link(parent, hub);
        hubs.push(hub);
        hubOf.set(parent.id, hubs);
      }
      const child = addNode(chain, "trail", { category: "attacker", hop: hub.hop + 1 });
      link(hub, child);
      pool.push(child);
    } else {
      const child = addNode(chain, "trail", { category: "attacker", hop: parent.hop + 1 });
      link(parent, child);
      pool.push(child);
    }
  }
}

const chainNodes = (chain: Chain, roles: TrailNode["role"][]) =>
  Array.from(nodes.values()).filter((n) => n.chain === chain && roles.includes(n.role));

// Exits: service nodes, one per chain and entity, fed by edges from trail nodes.
const serviceNodes = new Map<string, GNode>();
function serviceFor(chain: Chain, category: ExitCategory, entity: string): GNode {
  const k = `${chain}:${entity}`;
  let s = serviceNodes.get(k);
  if (!s) {
    const label = SERVICE_LABEL[entity] ?? (category === "exchange" ? `${entity} deposit` : entity);
    s = addNode(chain, "service", { category, label });
    serviceNodes.set(k, s);
  }
  return s;
}

const fixedOut = new Map<string, number>(); // edge key -> usd, for edges into services and counterparties
for (const chain of CHAINS) {
  const vis = SPEC[chain].visible;
  for (const [cat, total] of Object.entries(EXITS[chain]) as [ExitCategory, number][]) {
    for (const [entity, share] of ENTITIES[cat][chain] ?? []) {
      const value = total * share * vis;
      const service = serviceFor(chain, cat, entity);
      const sources =
        chain === "bitcoin" ? chainNodes(chain, ["hub"]) : chainNodes(chain, ["trail", "seed"]).filter((n) => n.label !== "EIP-7702 delegate contract");
      const k = Math.max(2, Math.min(14, Math.round(value / 6e6)));
      const parts = split(value, k, 1.4);
      for (const part of parts) {
        const src = weightedPick(sources, (n) => 1 + n.hop * 0.6);
        link(src, service);
        const key = edgeKey(src.id, service.id);
        fixedOut.set(key, (fixedOut.get(key) ?? 0) + part);
        src.outFixed += part;
      }
    }
  }
}

// Counterparties: received from the trail, but most of their value came from elsewhere.
for (let i = 0; i < 18; i++) {
  const chain = pick(["ethereum", "ethereum", "bitcoin", "tron", "tron", "xrpl", "solana"] as Chain[]);
  const sources = chain === "bitcoin" ? chainNodes(chain, ["hub"]) : chainNodes(chain, ["trail"]);
  const src = pick(sources);
  const cp = addNode(chain, "counterparty", { category: "unknown", label: rnd() < 0.3 ? "OTC counterparty" : "" });
  link(src, cp);
  const value = Math.exp(between(Math.log(8e3), Math.log(2.4e6)));
  fixedOut.set(edgeKey(src.id, cp.id), value);
  src.outFixed += value;
  cp.received_usd = value;
  cp.held_usd = value * between(0, 0.6);
}

// THORChain conversions: Ethereum trail addresses swap ETH for bitcoin delivered to new addresses,
// which join the trail (the attacker chose them).
const ethHeldVis = (heldTheftByChain.get("ethereum") as number) * SPEC.ethereum.visible;
const convertedVis = CONVERTED_TO_BTC.usd * 0.92;
if (convertedVis >= ethHeldVis) throw new Error("converted value exceeds what is held from Ethereum");
{
  const sources = chainNodes("ethereum", ["trail"]).filter((n) => n.hop >= 2);
  const parts = split(convertedVis, 14, 1.3);
  for (const part of parts) {
    const src = pick(sources);
    const btc = addNode("bitcoin", "trail", { category: "attacker", hop: src.hop + 1, label: "" });
    link(src, btc, "bridge");
    btc.heldTheft = part;
  }
}

// Held value: dormant leaves, a few seeds, and some intermediate addresses.
for (const chain of CHAINS) {
  const vis = SPEC[chain].visible;
  let budget = (heldTheftByChain.get(chain) as number) * vis;
  if (chain === "ethereum") budget -= convertedVis;
  const candidates = chainNodes(chain, ["trail", "seed"]).filter((n) => !(chain === "bitcoin" && n.heldTheft > 0));
  const holders = candidates.filter((n) => (n.kids.length === 0 ? true : rnd() < (n.role === "seed" ? 0.3 : 0.15)));
  if (!holders.length) holders.push(candidates[0]);
  const weights = holders.map(() => Math.pow(rnd(), -0.85));
  const sum = weights.reduce((s, w) => s + w, 0);
  holders.forEach((n, i) => {
    n.heldTheft += (budget * weights[i]) / sum;
  });
}

// Frozen stablecoins sit at seed addresses (and a few first hops).
for (const f of [...FREEZES.map((x) => ({ ...x, late: false })), { ...LATE_FREEZE, addresses: 1, minute: 0, late: true }]) {
  const pool = chainNodes(f.chain, f.late ? ["trail"] : ["seed"]);
  const holders = pool.slice(0, Math.max(1, Math.min(f.addresses, pool.length)));
  const parts = split(f.amount, holders.length, 1.2);
  holders.forEach((n, i) => {
    n.frozen += parts[i];
    n.stable ??= f.symbol;
  });
}

// Aggregate bottom-up: what each node passed on plus what it kept.
const order = Array.from(nodes.values()).sort((a, b) => b.hop - a.hop);
for (const n of order) {
  if (n.role === "service" || n.role === "counterparty" || n.role === "victim") continue;
  let out = n.outFixed;
  for (const kidId of n.kids) {
    const kid = nodes.get(kidId) as GNode;
    if (kid.role === "service" || kid.role === "counterparty") continue;
    const share = kid.free / kid.parents.filter((p) => (nodes.get(p) as GNode).role !== "victim").length;
    out += share;
  }
  n.free = n.heldTheft + n.frozen + out;
}

// Edge values and node figures.
for (const e of edges.values()) {
  const s = nodes.get(e.source) as GNode;
  const t = nodes.get(e.target) as GNode;
  const fixed = fixedOut.get(edgeKey(s.id, t.id));
  if (fixed !== undefined) e.usd = fixed;
  else if (s.role === "victim") e.usd = t.free / t.parents.filter((p) => (nodes.get(p) as GNode).role === "victim").length;
  else e.usd = t.free / t.parents.filter((p) => (nodes.get(p) as GNode).role !== "victim").length;
}
for (const n of nodes.values()) {
  if (n.role === "service") {
    n.received_usd = Array.from(edges.values())
      .filter((e) => e.target === n.id)
      .reduce((s, e) => s + e.usd, 0);
  } else if (n.role === "victim") {
    n.received_usd = Array.from(edges.values())
      .filter((e) => e.source === n.id)
      .reduce((s, e) => s + e.usd, 0);
  } else if (n.role !== "counterparty") {
    n.received_usd = n.free;
    n.held_usd = n.heldTheft * nowRatio(n.chain);
  }
}
for (const n of nodes.values()) {
  if (n.role === "service" || n.role === "counterparty") {
    const hops = n.parents.map((p) => (nodes.get(p) as GNode).hop);
    n.hop = Math.min(...hops) + 1;
  }
}

// Timing: the theft, then each hop some hours after the one before.
const trancheFor = (chain: Chain, symbol?: string) => {
  const ts = TRANCHES.filter((t) => t.chain === chain && (!symbol || t.symbol === symbol));
  return ts.length ? weightedPick(ts, trancheUsd) : weightedPick(TRANCHES.filter((t) => t.chain === chain), trancheUsd);
};
const theftTime = (t: Tranche) => atMinute(between(t.from, t.to)) + Math.floor(rnd() * 50_000);
const clampTime = (ms: number) => Math.min(ms, DATA_THROUGH - 2 * 60_000);

interface Xfer {
  uid: string;
  chain: Chain;
  tx: string;
  ts: number;
  sender: string;
  receiver: string;
  symbol: string;
  amount: number;
  usd: number;
  kind: string;
}
const transfers: Xfer[] = [];
let uidCounter = 0;
const pushTransfer = (x: Omit<Xfer, "uid">) => transfers.push({ ...x, uid: `${x.chain}:${(++uidCounter).toString(36).padStart(6, "0")}` });
const transferKind = (chain: Chain, symbol: string, bridge = false) =>
  bridge ? "bridge" : chain === "bitcoin" ? "utxo" : symbol === NATIVE[chain] ? "native" : "token";

// Theft edges: victim to seed, in the drain window of the asset they carried.
for (const e of edges.values()) {
  const s = nodes.get(e.source) as GNode;
  const t = nodes.get(e.target) as GNode;
  if (s.role !== "victim") continue;
  e.theft = true;
  const n = Math.max(1, Math.min(14, Math.round(e.usd / 9e6)));
  const parts = split(e.usd, n, 1.1);
  const times: number[] = [];
  for (const part of parts) {
    const tranche = t.stable && rnd() < t.frozen / Math.max(1, t.free) ? trancheFor(t.chain, t.stable) : trancheFor(t.chain);
    const ts = theftTime(tranche);
    times.push(ts);
    pushTransfer({
      chain: t.chain,
      tx: txHash(t.chain),
      ts: sec(ts),
      sender: s.id,
      receiver: t.id,
      symbol: tranche.symbol,
      amount: part / priceAtTheft(tranche.symbol),
      usd: part,
      kind: transferKind(t.chain, tranche.symbol),
    });
  }
  e.count = n;
  e.first_ts = sec(Math.min(...times));
  e.last_ts = sec(Math.max(...times));
}
// The last transfer of the theft: 2,961.9 ETH at 23:56 UTC, on one of the large Ethereum edges.
{
  const e = Array.from(edges.values())
    .filter((x) => x.theft && x.source.startsWith("ethereum:") && x.count > 2)
    .sort((a, b) => b.usd - a.usd)[0];
  const rows = transfers.filter((x) => x.sender === e.source && x.receiver === e.target);
  const last = rows[rows.length - 1];
  const lastUsd = 2_961.9 * PRICE_THEFT.ETH;
  const others = rows.slice(0, -1);
  const scale = (e.usd - lastUsd) / others.reduce((s, x) => s + x.usd, 0);
  for (const x of others) {
    x.usd *= scale;
    x.amount *= scale;
  }
  Object.assign(last, { ts: sec(LAST_THEFT), symbol: "ETH", amount: 2_961.9, usd: lastUsd, kind: "native" });
  e.last_ts = sec(LAST_THEFT);
}

for (const n of nodes.values()) {
  if (n.role === "victim") continue;
  const incoming = Array.from(edges.values()).filter((e) => e.target === n.id && e.theft);
  if (incoming.length) n.first_ts = Math.min(...incoming.map((e) => e.first_ts));
}
for (const n of nodes.values()) {
  if (n.role !== "victim") continue;
  const out = Array.from(edges.values()).filter((e) => e.source === n.id);
  n.first_ts = Math.min(...out.map((e) => e.first_ts));
}

// Downstream edges in hop order, so each starts after its source was funded.
const byHop = Array.from(nodes.values()).sort((a, b) => a.hop - b.hop);
for (const s of byHop) {
  if (s.role === "victim" || s.role === "service" || s.role === "counterparty") continue;
  for (const kidId of s.kids) {
    const t = nodes.get(kidId) as GNode;
    const e = edges.get(edgeKey(s.id, t.id)) as TrailEdge;
    const start = s.first_ts * 1000;
    const isExit = t.role === "service" || t.role === "counterparty";
    const delay = isExit ? 10 * 60_000 + expo(26 * H) : s.role === "hub" ? 0 : 2 * 60_000 + expo(SPEC[s.chain].delayH * H);
    let first = clampTime(start + delay);
    if (first < start) first = start;
    const count = s.role === "hub" ? 1 : isExit ? intBetween(1, 7) : t.role === "hub" ? 1 : intBetween(1, 4);
    const last = count > 1 ? clampTime(first + expo((isExit ? 14 : 3) * H)) : first;
    e.count = count;
    e.first_ts = sec(first);
    e.last_ts = sec(last);
    if (!isExit && (t.first_ts === 0 || e.first_ts < t.first_ts)) t.first_ts = e.first_ts;
  }
}
for (const n of nodes.values()) {
  if (n.role === "service" || n.role === "counterparty") {
    const inc = Array.from(edges.values()).filter((e) => e.target === n.id);
    n.first_ts = Math.min(...inc.map((e) => e.first_ts));
  }
}

// The laundering is still going on: some edges carry a transfer in the last hours of the data.
{
  const candidates = Array.from(edges.values()).filter((e) => !e.theft && (nodes.get(e.source) as GNode).role !== "hub");
  for (let i = 0; i < 34; i++) {
    const e = pick(candidates);
    const recent = sec(DATA_THROUGH - between(3, 9 * 60) * 60_000);
    if (recent <= e.last_ts || recent <= e.first_ts) continue;
    e.count += 1;
    e.last_ts = recent;
  }
}

// Expand the downstream edges into individual transfers (the Parquet rows and the feed).
const hubTx = (hub: GNode) => hub.address;
for (const e of edges.values()) {
  if (e.theft) continue;
  const s = nodes.get(e.source) as GNode;
  const t = nodes.get(e.target) as GNode;
  const chain = t.chain;
  const symbol = e.kind === "bridge" ? "BTC" : NATIVE[chain];
  const parts = split(e.usd, e.count, 1.05);
  for (let i = 0; i < e.count; i++) {
    const ts = e.count === 1 ? e.first_ts : Math.round(e.first_ts + ((e.last_ts - e.first_ts) * i) / (e.count - 1));
    const tx = s.role === "hub" ? hubTx(s) : t.role === "hub" ? hubTx(t) : txHash(e.kind === "bridge" ? "bitcoin" : chain);
    pushTransfer({
      chain,
      tx,
      ts,
      sender: s.id,
      receiver: t.id,
      symbol,
      amount: parts[i] / PRICE_THEFT[symbol],
      usd: parts[i],
      kind: transferKind(chain, symbol, e.kind === "bridge"),
    });
  }
}

// ---------------------------------------------------------------------------------------------
// Documents

const allNodes = Array.from(nodes.values());
const allEdges = Array.from(edges.values());
const totalNodes = 9_214;
const totalEdges = 15_872;

const graph: TrailGraph = {
  nodes: allNodes
    .map((n) => ({
      id: n.id,
      chain: n.chain,
      address: n.address,
      role: n.role,
      category: n.category,
      label: n.label,
      hop: n.hop,
      received_usd: round(n.received_usd),
      held_usd: round(n.held_usd),
      first_ts: n.first_ts,
    }))
    .sort((a, b) => b.received_usd - a.received_usd),
  edges: allEdges.map((e) => ({
    source: e.source,
    target: e.target,
    usd: round(e.usd),
    count: e.count,
    first_ts: e.first_ts,
    last_ts: e.last_ts,
    kind: e.kind,
  })),
  truncated: true,
  total_nodes: totalNodes,
  total_edges: totalEdges,
};

const caseFile: CaseFile = {
  id: "binance-2026-10",
  title: "Binance, 29 October 2026",
  victim: "Binance",
  attribution: "TraderTraitor (North Korea), per the FBI alert of 31 October 2026",
  window: { start: "2026-10-29T19:00:00Z", end: "2026-10-30T00:10:00Z" },
  reported: {
    stolen_usd: 6_430_000_000,
    stolen_usd_source: "Elliptic, 3 November 2026 (Binance put affected user funds at 6.34 billion)",
    frozen_usd: 2_850_000_000,
    frozen_usd_source: "Elliptic, 6 November 2026, frozen or seized",
    chains: 11,
  },
  timeline: [
    { at: "2026-06", what: "A Wallet DevOps engineer installs a tampered Homebrew package, found through a search ad while looking for an AI coding tool.", source: "binance-report" },
    { at: "2026-09", what: "A second engineer runs an interview 'assignment' from a fake recruiter. It replaces pyenv in the Homebrew directory with a backdoor.", source: "binance-report" },
    { at: "2026-10-20", what: "Through the second engineer's machine and an internal AI tool, the attacker takes over two more engineers' computers and finds a token with container deployment rights.", source: "binance-report" },
    { at: "2026-10-29", what: "Hours before the drain, a container deployed with stolen credentials reaches the wallet signing service. It signs what the attacker asks, including 50 EIP-7702 delegations.", source: "tangem" },
    { at: "2026-10-29T20:05:00Z", what: "The drain begins on the XRP Ledger: 233.46 million XRP leave five hot wallets in four minutes.", source: "tangem" },
    { at: "2026-10-29T20:20:00Z", what: "Ethereum and Bitcoin follow. 18,270 BTC is split across 19 addresses within half an hour.", source: "elliptic" },
    { at: "2026-10-29", what: "Tether freezes the stolen USDT on Ethereum, 37 minutes after it arrived. 434 million USDT had already been swapped for ETH.", source: "elliptic" },
    { at: "2026-10-29T23:56:00Z", what: "The last transfer, 2,961.9 ETH. The delegations had kept working after withdrawals were suspended.", source: "thoropass" },
    { at: "2026-10-30T00:10:00Z", what: "Binance revokes the EIP-7702 delegations.", source: "thoropass" },
    { at: "2026-10-31", what: "The FBI attributes the theft to TraderTraitor and publishes 628 addresses.", source: "fbi" },
  ],
  sources: [
    { id: "fbi", title: "FBI: North Korea Responsible for $6 Billion+ Binance Attack (31 October 2026)", url: "https://www.fbi.gov/investigate/cyber/alerts/2026/north-korea-responsible-for-6-billion-binance-attack" },
    { id: "binance-report", title: "Binance: 29 October Binance Security Incident, Initial Findings and Response Update", url: "https://x.com/binance/status/2117626126214504729" },
    { id: "elliptic", title: "Elliptic: How North Korea is laundering the $6.4 billion stolen from Binance", url: "https://www.elliptic.co/insights/how-north-korea-is-laundering-the-6-4-billion-stolen-from-binance" },
    { id: "thoropass", title: "Thoropass: Binance Breach Explained", url: "https://www.thoropass.com/blog/binance-breach-2026" },
    { id: "tangem", title: "Tangem: Binance Hack 2026, How Attackers Stole $6.34B", url: "https://tangem.com/en/blog/post/binance-hack-2026-how-it-happened/" },
    { id: "cnbc", title: "CNBC: Binance co-CEO says hackers used fake job interviews and an AI tool", url: "https://www.cnbc.com/2026/11/03/binance-co-ceo-teng-says-hackers-used-fake-job-interviews-to-steal-6point3-billion-in-crypto.html" },
  ],
};

// Held now, by where the value sits today (current prices).
const heldNowRows: { chain: Chain; usd: number; addresses: number; symbol: string }[] = [];
const HELD_ADDRESSES: Record<Chain, number> = { bitcoin: 418, ethereum: 241, tron: 7_742, xrpl: 71, solana: 46, avalanche: 12, arbitrum: 9, optimism: 6, base: 5, polygon: 8, near: 4 };
{
  // Value stolen on each chain that is still held, moved to where it sits now.
  const where = new Map<Chain, number>();
  for (const c of CHAINS) where.set(c, heldTheftByChain.get(c) as number);
  where.set("ethereum", (where.get("ethereum") as number) - CONVERTED_TO_BTC.usd);
  where.set("bitcoin", (where.get("bitcoin") as number) + CONVERTED_TO_BTC.usd);
  for (const c of CHAINS) {
    const usd = (where.get(c) as number) * nowRatio(c);
    heldNowRows.push({ chain: c, usd, addresses: HELD_ADDRESSES[c], symbol: NATIVE[c] });
  }
  heldNowRows.sort((a, b) => b.usd - a.usd);
}
const heldNowTotal = heldNowRows.reduce((s, r) => s + r.usd, 0);

const exitsByCategory = new Map<ExitCategory, { usd: number; entities: Map<string, number> }>();
for (const c of CHAINS) {
  for (const [cat, total] of Object.entries(EXITS[c]) as [ExitCategory, number][]) {
    const row = exitsByCategory.get(cat) ?? { usd: 0, entities: new Map() };
    row.usd += total;
    for (const [entity, share] of ENTITIES[cat][c] ?? []) row.entities.set(entity, (row.entities.get(entity) ?? 0) + total * share);
    exitsByCategory.set(cat, row);
  }
}
const exitedTotal = Array.from(exitsByCategory.values()).reduce((s, r) => s + r.usd, 0);
const frozenTotal = Array.from(frozenByChain.values()).reduce((s, v) => s + v, 0);

const theftTransfers = transfers.filter((x) => (nodes.get(x.sender) as GNode | undefined)?.role === "victim");
const lastMovement = Math.max(...transfers.filter((x) => x.ts * 1000 <= DATA_THROUGH).map((x) => x.ts)) * 1000;

const summary: Summary = {
  generated_at: iso(GENERATED),
  data_through: iso(DATA_THROUGH),
  last_movement_at: iso(lastMovement),
  stolen: {
    usd_at_theft: round(STOLEN_TOTAL, 0),
    reported_usd: caseFile.reported.stolen_usd,
    by_chain: CHAINS.map((c) => ({ chain: c, usd: round(stolenByChain.get(c) as number, 0) })).sort((a, b) => b.usd - a.usd),
    by_asset: (() => {
      const m = new Map<string, { symbol: string; chain: Chain; amount: number; usd: number }>();
      for (const t of TRANCHES) {
        const k = `${t.chain}:${t.symbol}`;
        const row = m.get(k) ?? { symbol: t.symbol, chain: t.chain, amount: 0, usd: 0 };
        row.amount += t.amount;
        row.usd += trancheUsd(t);
        m.set(k, row);
      }
      return Array.from(m.values())
        .map((r) => ({ ...r, amount: round(r.amount, 4), usd: round(r.usd, 0) }))
        .sort((a, b) => b.usd - a.usd);
    })(),
    transfers: theftTransfers.length,
    victim_wallets: allNodes.filter((n) => n.role === "victim").length,
    first_at: iso(FIRST_THEFT),
    last_at: iso(LAST_THEFT),
  },
  now: {
    held: {
      usd: round(heldNowTotal, 0),
      addresses: Object.values(HELD_ADDRESSES).reduce((s, v) => s + v, 0),
      by_chain: heldNowRows.map((r) => ({ chain: r.chain, usd: round(r.usd, 0), addresses: r.addresses })),
      by_asset: heldNowRows.map((r) => ({ symbol: r.symbol, chain: r.chain, amount: round(r.usd / PRICE_NOW[r.symbol], 2), usd: round(r.usd, 0) })),
    },
    frozen: {
      usd: round(frozenTotal, 0),
      by_issuer: FREEZES.map((f) => {
        const late = f.issuer === LATE_FREEZE.issuer && f.chain === LATE_FREEZE.chain && f.symbol === LATE_FREEZE.symbol;
        return {
          issuer: f.issuer,
          chain: f.chain,
          symbol: f.symbol,
          amount: f.amount + (late ? LATE_FREEZE.amount : 0),
          usd: f.amount + (late ? LATE_FREEZE.amount : 0),
          addresses: f.addresses + (late ? 1 : 0),
          first_at: iso(atMinute(f.minute)),
        };
      }).sort((a, b) => b.usd - a.usd),
    },
    exited: {
      usd: round(exitedTotal, 0),
      by_category: Array.from(exitsByCategory.entries())
        .map(([category, r]) => ({
          category,
          usd: round(r.usd, 0),
          count: Math.round(r.usd / 1.4e6) + intBetween(3, 40),
          entities: Array.from(r.entities.entries())
            .map(([name, usd]) => ({ name, usd: round(usd, 0), count: Math.max(1, Math.round(usd / 1.6e6)) }))
            .sort((a, b) => b.usd - a.usd),
        }))
        .sort((a, b) => b.usd - a.usd),
    },
  },
  converted: { to_btc: CONVERTED_TO_BTC },
  trail: { addresses: totalNodes, chains: [...CHAINS], max_hop: Math.max(...allNodes.map((n) => n.hop)), transfers: 48_906, services_touched: 63 },
};

// Flows: victim to chain of theft to where the value is now (theft prices, so bands add up).
const flows: FlowGraph = {
  nodes: [
    { id: "victim", label: "Binance", kind: "victim" },
    ...CHAINS.map((c) => ({ id: `chain:${c}`, label: chainLabel(c), kind: "chain" as const })),
    { id: "state:held", label: "Held by attacker addresses", kind: "state" },
    { id: "state:frozen", label: "Frozen by issuers", kind: "state" },
    { id: "exit:bridge", label: "Bridges", kind: "category" },
    { id: "exit:mixer", label: "Mixers and privacy pools", kind: "category" },
    { id: "exit:exchange", label: "Exchanges", kind: "category" },
    { id: "exit:dex", label: "DEX pools", kind: "category" },
    { id: "exit:lending", label: "Lending", kind: "category" },
    { id: "exit:staking", label: "Staking", kind: "category" },
    { id: "exit:other", label: "Other services", kind: "category" },
  ],
  links: [
    ...CHAINS.map((c) => ({ source: "victim", target: `chain:${c}`, usd: round(stolenByChain.get(c) as number, 0) })),
    ...CHAINS.map((c) => ({ source: `chain:${c}`, target: "state:held", usd: round(heldTheftByChain.get(c) as number, 0) })),
    ...CHAINS.filter((c) => frozenByChain.has(c)).map((c) => ({ source: `chain:${c}`, target: "state:frozen", usd: round(frozenByChain.get(c) as number, 0) })),
    ...CHAINS.flatMap((c) =>
      (Object.entries(EXITS[c]) as [ExitCategory, number][]).map(([cat, usd]) => ({ source: `chain:${c}`, target: `exit:${cat}`, usd: round(usd, 0) })),
    ),
  ].filter((l) => l.usd > 0),
};

function chainLabel(c: Chain): string {
  return {
    ethereum: "Ethereum",
    optimism: "Optimism",
    arbitrum: "Arbitrum",
    base: "Base",
    polygon: "Polygon",
    avalanche: "Avalanche",
    bitcoin: "Bitcoin",
    tron: "Tron",
    xrpl: "XRP Ledger",
    solana: "Solana",
    near: "NEAR",
  }[c];
}

// Feed: the newest 200 movements on the trail, plus the freezes as they happened.
function feedKind(e: TrailEdge, target: GNode): FeedItem["kind"] {
  if (e.kind === "bridge") return "bridge";
  if (target.role !== "service") return "move";
  if (target.category === "mixer") return "mix";
  if (target.category === "dex") return "swap";
  if (target.category === "bridge") return "bridge";
  return "exit";
}
function feedNote(kind: FeedItem["kind"], s: GNode, t: GNode, e: TrailEdge): string {
  const entity = t.label;
  if (e.kind === "bridge") return "THORChain swap: ETH in, BTC delivered to a new address";
  switch (kind) {
    case "mix":
      return `Entered ${entity}`;
    case "swap":
      return `Swapped through ${entity}`;
    case "bridge":
      return `Sent across ${entity}`;
    case "exit":
      return t.category === "exchange" ? `Deposit to ${entity}` : `Sent to ${entity}`;
    default:
      if (t.role === "counterparty") return "Paid to an address outside the trail";
      if (s.chain === "bitcoin") return `Peel chain, hop ${t.hop}`;
      if (s.label.includes("fan-out")) return "Fan-out to a fresh address";
      return `Moved along the trail, hop ${t.hop}`;
  }
}

const feedItems: FeedItem[] = [];
for (const x of transfers) {
  if (x.ts * 1000 > DATA_THROUGH) continue;
  const s = nodes.get(x.sender) as GNode;
  const t = nodes.get(x.receiver) as GNode;
  if (s.role === "victim" || t.role === "hub") continue;
  const e = edges.get(edgeKey(s.id, t.id)) as TrailEdge;
  // A bitcoin output is shown from the address that funded its transaction.
  const from = s.role === "hub" ? (nodes.get(s.parents[0]) as GNode) : s;
  const kind = feedKind(e, t);
  feedItems.push({
    ts: iso(x.ts * 1000),
    chain: x.chain,
    tx: x.tx,
    explorer_url: EXPLORER[x.chain](x.tx),
    kind,
    from: from.address,
    from_label: from.label,
    to: t.address,
    to_label: t.label,
    symbol: x.symbol,
    amount: round(x.amount, x.symbol === "BTC" || x.symbol === "ETH" ? 4 : 2),
    usd: round(x.usd, 0),
    note: feedNote(kind, from, t, e),
  });
}
{
  const holder = chainNodes("tron", ["trail"]).find((n) => n.frozen > 0) as GNode;
  const tx = txHash("tron");
  feedItems.push({
    ts: iso(LATE_FREEZE.at),
    chain: "tron",
    tx,
    explorer_url: EXPLORER.tron(tx),
    kind: "freeze",
    from: holder.address,
    from_label: holder.label,
    to: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
    to_label: "Tether USDT contract",
    symbol: "USDT",
    amount: LATE_FREEZE.amount,
    usd: LATE_FREEZE.amount,
    note: "Tether froze 3.1M USDT bought back for an over-the-counter sale",
  });
}
feedItems.sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts));
const feed: Feed = { generated_at: iso(GENERATED), items: feedItems.slice(0, 200) };

// Golden hour: the drain, the freezable stablecoins and what escaped, minute by minute.
const MINUTES = Array.from({ length: 246 }, (_, i) => i);
const ramp = (m: number, from: number, to: number) => (to <= from ? (m >= from ? 1 : 0) : Math.min(1, Math.max(0, (m - from) / (to - from))));
const drainedAt = (m: number) => TRANCHES.reduce((s, t) => s + trancheUsd(t) * ramp(m, t.from, t.to), 0);
const escapedAt = (m: number) => SWAPS.reduce((s, w) => s + w.amount * ramp(m, w.from, w.to), 0);
const frozenAt = (m: number) => FREEZES.reduce((s, f) => s + (m >= f.minute ? f.amount : 0), 0);
const arrivedStableAt = (m: number) => TRANCHES.filter((t) => STABLE.has(t.symbol)).reduce((s, t) => s + trancheUsd(t) * ramp(m, t.from, t.to), 0);
const freezableAt = (m: number) => Math.max(0, arrivedStableAt(m) - escapedAt(m) - frozenAt(m));
const escapedTotal = SWAPS.reduce((s, w) => s + w.amount, 0);
const ISSUER_DELAY = 5;
const HALT_DELAY = 2;

const goldenHour: GoldenHour = {
  t0: iso(T0),
  minutes: MINUTES,
  drained_usd: MINUTES.map((m) => round(drainedAt(m), 0)),
  freezable_usd: MINUTES.map((m) => round(freezableAt(m), 0)),
  escaped_usd: MINUTES.map((m) => round(escapedAt(m), 0)),
  events: [
    { minute: 0, at: iso(atMinute(0)), label: "Drain begins on the XRP Ledger", kind: "theft", source: "tangem" },
    { minute: 0.6, at: iso(atMinute(0.6)), label: "Watch raises its first alert (replay)", kind: "detect", source: "detector" },
    { minute: 15, at: iso(atMinute(15)), label: "Ethereum and Bitcoin follow", kind: "theft", source: "elliptic" },
    { minute: 22, at: iso(atMinute(22)), label: "First stolen USDT swapped for ETH", kind: "swap", source: "elliptic" },
    { minute: 46, at: iso(atMinute(46)), label: "Circle freezes USDC on Ethereum", kind: "freeze", source: "elliptic" },
    { minute: 57, at: iso(atMinute(57)), label: "Tether freezes USDT on Ethereum, 37 minutes after it arrived", kind: "freeze", source: "elliptic" },
    { minute: 95, at: iso(atMinute(95)), label: "Tether freezes USDT on Tron", kind: "freeze", source: "elliptic" },
    { minute: 231, at: iso(LAST_THEFT), label: "Last transfer: 2,961.9 ETH", kind: "theft", source: "thoropass" },
    { minute: 245, at: iso(atMinute(245)), label: "Binance revokes the EIP-7702 delegations", kind: "revoke", source: "thoropass" },
  ],
  scenarios: [0, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 245].map((n) => ({
    response_minutes: n,
    freezable_saved_usd: round(escapedTotal - escapedAt(n + ISSUER_DELAY), 0),
    drain_prevented_usd: round(drainedAt(245) - drainedAt(n + HALT_DELAY), 0),
  })),
  notes: [
    "Freezable means a stablecoin whose issuer can freeze it, sitting at an attacker address and not yet swapped.",
    `A response that starts at minute N is assumed to reach issuers ${ISSUER_DELAY} minutes later. Anything swapped before then counts as lost.`,
    `Prevented drain assumes signing stops ${HALT_DELAY} minutes after minute N, including the EIP-7702 delegations. Transfers already signed still settle.`,
    "Values use prices at the time of the theft. Native coins (BTC, ETH, XRP, TRX) cannot be frozen by an issuer and only count toward the drain.",
    "Issuers decide whether to freeze. The scenario shows what was still within reach, not what would certainly have been frozen.",
  ],
};

const detectorReplay = [
  { chain: "xrpl", victim: 0, theft: "2026-10-29T20:05:04Z", alert: "2026-10-29T20:05:39Z", before: 61_200_000, rule: "drain-velocity" },
  { chain: "xrpl", victim: 1, theft: "2026-10-29T20:05:22Z", alert: "2026-10-29T20:05:51Z", before: 44_900_000, rule: "drain-velocity" },
  { chain: "ethereum", victim: 0, theft: "2026-10-29T20:20:11Z", alert: "2026-10-29T19:08:40Z", before: 0, rule: "delegation-change" },
  { chain: "ethereum", victim: 1, theft: "2026-10-29T20:20:47Z", alert: "2026-10-29T19:08:40Z", before: 0, rule: "delegation-change" },
  { chain: "bitcoin", victim: 0, theft: "2026-10-29T20:21:10Z", alert: "2026-10-29T20:22:28Z", before: 412_000_000, rule: "unknown-destination" },
  { chain: "tron", victim: 0, theft: "2026-10-29T20:27:02Z", alert: "2026-10-29T20:27:41Z", before: 38_400_000, rule: "fan-out" },
  { chain: "solana", victim: 0, theft: "2026-10-29T20:40:15Z", alert: "2026-10-29T20:41:16Z", before: 12_100_000, rule: "drain-velocity" },
  { chain: "base", victim: 0, theft: "2026-10-29T20:49:30Z", alert: "2026-10-29T19:08:40Z", before: 0, rule: "delegation-change" },
  { chain: "avalanche", victim: 0, theft: "2026-10-29T21:10:05Z", alert: "2026-10-29T21:12:44Z", before: 2_100_000, rule: "drain-velocity" },
  { chain: "near", victim: 0, theft: "2026-10-29T21:55:40Z", alert: null, before: 3_410_000, rule: "" },
] as const;

const detector: Detector = {
  replay: detectorReplay.map((r) => {
    const v = (victimsByChain.get(r.chain) as GNode[])[r.victim];
    const minutes = r.alert === null ? null : round((Date.parse(r.alert) - Date.parse(r.theft)) / 60_000, 1);
    return {
      chain: r.chain,
      wallet: v.address,
      label: v.label,
      first_theft_at: r.theft,
      first_alert_at: r.alert,
      minutes_after_first_theft: minutes,
      usd_before_alert: r.before,
      rule: r.rule,
    };
  }),
  baseline: { days: 30, wallets: 412, transactions: 1_842_377, alerts: 23, alerts_per_wallet_day: round(23 / (412 * 30), 5) },
  unread_blocks: 0,
  rules: [
    {
      id: "delegation-change",
      title: "Delegation on a custody wallet",
      description: "A custody wallet sets or changes an EIP-7702 delegation. Hot wallets never delegate, so any delegation is an incident.",
      severity: "critical",
    },
    {
      id: "post-halt-outflow",
      title: "Outflow after a halt",
      description: "Any value leaving a custody wallet after withdrawals were suspended.",
      severity: "critical",
    },
    {
      id: "drain-velocity",
      title: "Drain velocity",
      description: "Outflows above 20% of a wallet's balance within 10 minutes, measured against its own 30 day pattern.",
      severity: "critical",
    },
    {
      id: "unknown-destination",
      title: "Unknown destination",
      description: "A transfer above the wallet's usual size to an address with no withdrawal request and no history with the wallet.",
      severity: "high",
    },
    {
      id: "fan-out",
      title: "Fan-out",
      description: "One wallet pays more than 10 new addresses within 5 minutes.",
      severity: "high",
    },
    {
      id: "stablecoin-swap",
      title: "Stablecoin swap outside treasury hours",
      description: "A custody stablecoin balance is swapped on a DEX outside an approved treasury window.",
      severity: "medium",
    },
  ],
};

const ethVictim = (victimsByChain.get("ethereum") as GNode[])[3];
const ethDelegate = (seedsByChain.get("ethereum") as GNode[])[0];
const guard: GuardCases = {
  policy_yaml: `# Guard policy for custody wallets.
# Every signing request is evaluated here before the signing service sees it.
# A request is signed only when no rule denies or holds it. Deny wins over hold.
version: 1

scope:
  wallets: [hot, warm]
  chains: [ethereum, optimism, arbitrum, base, polygon, avalanche,
           bitcoin, tron, xrpl, solana, near]

intents:
  # Withdrawal intents come from the ledger, signed by the withdrawals service,
  # never from the caller that asks for the signature.
  source: withdrawals-ledger
  match: [destination, asset, amount]
  max_age: 15m

rules:
  - id: no-delegation
    description: Custody wallets never delegate their code or set an authority.
    match: { type: [eip7702_authorization, set_code, set_authority] }
    decision: deny

  - id: intent-required
    description: Every outgoing transfer matches a withdrawal intent.
    match: { type: [transfer, token_transfer] }
    require: intent
    on_missing: deny

  - id: halt
    description: While withdrawals are suspended, nothing leaves a custody wallet.
    match: { flag: withdrawals_suspended }
    decision: deny

  - id: velocity
    description: At most 5% of a hot wallet's balance in any 10 minutes.
    window: 10m
    limit: { share_of_balance: 0.05 }
    on_breach: hold

  - id: fan-out
    description: More than 10 new destinations in 5 minutes needs a second approver.
    window: 5m
    limit: { new_destinations: 10 }
    on_breach: hold

  - id: contract-allowlist
    description: Contract calls only to allowlisted contracts and selectors.
    match: { type: contract_call }
    require: { allowlist: custody-contracts }
    on_missing: hold

approvals:
  hold:
    approvers: 2
    from: [treasury-ops, security-oncall]
    timeout: 30m
    on_timeout: deny
`,
  cases: [
    {
      id: "binance-delegation",
      title: "An EIP-7702 delegation from a hot wallet",
      story: "The kind of request the signing service received on 29 October: authorize an outside contract to execute on behalf of a hot wallet. Fifty of these were signed.",
      chain: "ethereum",
      tx: null,
      request: {
        type: "eip7702_authorization",
        wallet: "hot-eth-04",
        from: ethVictim.address,
        delegate: ethDelegate.address,
        chain_id: 1,
        nonce: 48_211,
        requested_by: "svc/deploy-runner-7f9c",
      },
      decision: "deny",
      reasons: [
        { rule: "no-delegation", message: "Custody wallets never delegate their code. Refused before signing, whoever asks." },
        { rule: "intent-required", message: "No withdrawal intent references this request." },
      ],
    },
    {
      id: "no-intent-transfer",
      title: "A transfer no customer asked for",
      story: "A caller with valid service credentials asks for 12,400 ETH to an address that appears in no withdrawal request.",
      chain: "ethereum",
      tx: null,
      request: {
        type: "transfer",
        wallet: "hot-eth-02",
        to: address("ethereum"),
        asset: "ETH",
        amount: "12400",
        requested_by: "svc/deploy-runner-7f9c",
        intent_id: null,
      },
      decision: "deny",
      reasons: [{ rule: "intent-required", message: "No intent in the withdrawals ledger matches destination, asset and amount." }],
    },
    {
      id: "xrpl-sweep",
      title: "Five XRP wallets emptied in four minutes",
      story: "A burst of payments that would move most of each hot wallet's balance within minutes, as on the XRP Ledger at 20:05 UTC.",
      chain: "xrpl",
      tx: null,
      request: {
        type: "transfer",
        wallet: "hot-xrp-01",
        to: address("xrpl"),
        asset: "XRP",
        amount: "48200000",
        balance_share_10m: 0.61,
        intent_id: null,
      },
      decision: "deny",
      reasons: [
        { rule: "intent-required", message: "No matching withdrawal intent." },
        { rule: "velocity", message: "61% of the wallet's balance within 10 minutes; the limit is 5%." },
      ],
    },
    {
      id: "btc-split",
      title: "Bitcoin split across 19 new addresses",
      story: "One request pays 19 addresses the wallet has never paid before, the pattern of the 18,270 BTC split.",
      chain: "bitcoin",
      tx: null,
      request: {
        type: "transfer",
        wallet: "hot-btc-01",
        outputs: 19,
        new_destinations: 19,
        asset: "BTC",
        amount: "4820.5",
        intent_id: null,
      },
      decision: "deny",
      reasons: [
        { rule: "intent-required", message: "None of the 19 outputs matches a withdrawal intent." },
        { rule: "fan-out", message: "19 new destinations in one request; more than 10 needs a second approver." },
      ],
    },
    {
      id: "after-halt",
      title: "Signing after withdrawals were suspended",
      story: "Withdrawals are suspended, but a request arrives anyway. On 29 October the delegations kept moving funds after the halt.",
      chain: "ethereum",
      tx: null,
      request: {
        type: "token_transfer",
        wallet: "hot-eth-05",
        to: address("ethereum"),
        asset: "USDT",
        amount: "25000000",
        flags: ["withdrawals_suspended"],
      },
      decision: "deny",
      reasons: [{ rule: "halt", message: "Withdrawals are suspended. Nothing leaves a custody wallet until the halt is lifted." }],
    },
    {
      id: "unknown-contract",
      title: "A call to a contract nobody approved",
      story: "A contract call from a hot wallet to an address that is not on the custody allowlist.",
      chain: "arbitrum",
      tx: null,
      request: {
        type: "contract_call",
        wallet: "hot-arb-01",
        to: address("arbitrum"),
        selector: "0x095ea7b3",
        decoded: { method: "approve", spender: address("arbitrum"), amount: "unlimited" },
      },
      decision: "hold",
      reasons: [{ rule: "contract-allowlist", message: "The contract is not on the custody allowlist. Held for two approvers." }],
    },
    {
      id: "treasury-move",
      title: "A large treasury move with an intent behind it",
      story: "Treasury moves 3,000 BTC to cold storage. The intent matches, but the size breaches the velocity limit, so it waits for two approvers.",
      chain: "bitcoin",
      tx: null,
      request: {
        type: "transfer",
        wallet: "hot-btc-02",
        to: address("bitcoin"),
        asset: "BTC",
        amount: "3000",
        intent_id: "TRS-2026-1029-004",
        balance_share_10m: 0.18,
      },
      decision: "hold",
      reasons: [
        { rule: "intent-required", message: "Matches treasury intent TRS-2026-1029-004." },
        { rule: "velocity", message: "18% of the wallet's balance within 10 minutes; held for two approvers." },
      ],
    },
    {
      id: "customer-withdrawal",
      title: "An ordinary customer withdrawal",
      story: "A customer withdraws 2.5 ETH. Destination, asset and amount match the intent in the ledger, and the wallet is well inside its limits.",
      chain: "ethereum",
      tx: txHash("ethereum"),
      request: {
        type: "transfer",
        wallet: "hot-eth-01",
        to: address("ethereum"),
        asset: "ETH",
        amount: "2.5",
        intent_id: "W-88213-0042",
        balance_share_10m: 0.0004,
      },
      decision: "allow",
      reasons: [
        { rule: "intent-required", message: "Matches withdrawal W-88213-0042: destination, asset and amount agree." },
        { rule: "velocity", message: "0.04% of balance in the last 10 minutes." },
      ],
    },
  ],
};

const meta: Meta = {
  generated_at: iso(GENERATED),
  version: "0.1.0",
  tick: { at: iso(GENERATED - 41_000), duration_s: 41.2, fetched: 1_284, joined: 37, errors: 0 },
  sources_ok: { etherscan: true, mempool: true, tronscan: true, xrpscan: true, solana: true, thorchain: true, nearblocks: false, prices: true },
};

// Screening shards: a handful, chosen so the top addresses of each kind resolve in development.
const exposureFor = (role: string): ScreenEntry["exposure"] => (role === "seed" || role === "trail" ? "trail" : "direct");
function reasonFor(n: GNode | TrailNode): string {
  if (n.role === "seed") return "Named in the FBI alert of 31 October 2026 as holding or having held stolen assets.";
  if (n.role === "trail") {
    const share = 62 + Math.floor(rnd() * 37);
    return `${share}% of the value it ever received came from trail addresses (hop ${n.hop}).`;
  }
  if (n.role === "service") return `A ${n.category} service that received value directly from the trail. The trail stops here.`;
  return "Received value directly from a trail address, but most of its value came from elsewhere, so it did not join the trail.";
}
const screenable = allNodes.filter((n) => n.role !== "victim" && n.role !== "hub");
// The screening key is the address alone, canonically spelled, so every chain reading of an address
// shares one key and one shard (goldenhour.screen.canonical).
const canonical = (a: string) => (/^0x[0-9a-fA-F]{40}$/.test(a) || /^bc1[02-9ac-hj-np-z]{11,71}$/i.test(a) ? a.toLowerCase() : a);
const screenKey = (a: string) => sha256(canonical(a));
const keyed = screenable.map((n) => ({ n, key: screenKey(n.address) }));
const want = new Set<string>();
const top = (role: string, k: number, by: (n: GNode) => number) =>
  keyed
    .filter((x) => x.n.role === role)
    .sort((a, b) => by(b.n) - by(a.n))
    .slice(0, k)
    .forEach((x) => want.add(x.key.slice(0, 3)));
top("seed", 5, (n) => n.held_usd);
top("trail", 5, (n) => n.held_usd);
top("counterparty", 2, (n) => n.received_usd);
top("service", 2, (n) => n.received_usd);
const shards = new Map<string, Record<string, ScreenEntry[]>>();
for (const prefix of want) shards.set(prefix, {});
for (const { n, key } of keyed) {
  const shard = shards.get(key.slice(0, 3));
  if (!shard) continue;
  const exposure = exposureFor(n.role);
  (shard[key] ??= []).push({
    node: n.id,
    chain: n.chain,
    address: n.address,
    role: n.role,
    category: n.category,
    label: n.label,
    hop: n.hop,
    received_usd: round(n.received_usd),
    held_usd: round(n.held_usd),
    first_ts: n.first_ts,
    reason: reasonFor(n),
    exposure,
  });
}
// Each shard also holds addresses outside the top of the graph, as a real shard would.
for (const [prefix, shard] of shards) {
  const extra = intBetween(1, 3);
  for (let i = 0; i < extra; i++) {
    const chain = pick(["ethereum", "tron", "bitcoin", "ethereum", "solana"] as Chain[]);
    for (;;) {
      const addr = address(chain);
      const id = `${chain}:${addr}`;
      const key = screenKey(addr);
      if (!key.startsWith(prefix)) continue;
      const indirect = rnd() < 0.5;
      const received = Math.exp(between(Math.log(2e3), Math.log(4e5)));
      const entry = {
        node: id,
        chain,
        address: addr,
        role: indirect ? "counterparty" : "trail",
        category: indirect ? "unknown" : "attacker",
        label: "",
        hop: intBetween(4, 11),
        received_usd: round(received),
        held_usd: round(received * between(0, 0.9)),
        first_ts: sec(T0 + between(2, 180) * H),
        reason: "",
        exposure: (indirect ? "direct" : "trail") as ScreenEntry["exposure"],
      };
      entry.reason = reasonFor(entry as unknown as TrailNode);
      (shard[key] ??= []).push(entry);
      break;
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Write

function write(rel: string, doc: unknown) {
  const file = join(OUT, rel);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(doc, null, 1)}\n`);
}

rmSync(join(OUT, "screen"), { recursive: true, force: true });
write("case.json", caseFile);
write("summary.json", summary);
write("flows.json", flows);
write("feed.json", feed);
write("graph.json", graph);
write("golden-hour.json", goldenHour);
write("detector.json", detector);
write("guard.json", guard);
write("meta.json", meta);
for (const [prefix, entries] of shards) write(`screen/${prefix}.json`, { v: 2, entries });

// Parquet: the transfers and nodes tables, written by DuckDB through uv (Python) if present.
const tmp = join(root, "node_modules", ".cache", "goldenhour-fixtures");
mkdirSync(tmp, { recursive: true });
const transfersFile = join(tmp, "transfers.ndjson");
const nodesFile = join(tmp, "nodes.ndjson");
writeFileSync(
  transfersFile,
  transfers
    .sort((a, b) => a.ts - b.ts)
    .map((x) => JSON.stringify({ ...x, amount: round(x.amount, 8), usd: round(x.usd, 2) }))
    .join("\n"),
);
writeFileSync(
  nodesFile,
  allNodes
    .map((n) =>
      JSON.stringify({
        node: n.id,
        chain: n.chain,
        address: n.address,
        role: n.role,
        category: n.category,
        label: n.label,
        hop: n.hop,
        received_usd: round(n.received_usd),
        held_usd: round(n.held_usd),
      }),
    )
    .join("\n"),
);
const dataDir = join(OUT, "data");
mkdirSync(dataDir, { recursive: true });
const py = `
import sys, duckdb
t, n, out = sys.argv[1], sys.argv[2], sys.argv[3]
con = duckdb.connect()
con.execute(f"""COPY (SELECT uid::VARCHAR AS uid, chain::VARCHAR AS chain, tx::VARCHAR AS tx, ts::BIGINT AS ts,
  sender::VARCHAR AS sender, receiver::VARCHAR AS receiver, symbol::VARCHAR AS symbol, amount::DOUBLE AS amount,
  usd::DOUBLE AS usd, kind::VARCHAR AS kind FROM read_json_auto('{t}', format='newline_delimited') ORDER BY ts)
  TO '{out}/transfers.parquet' (FORMAT parquet, COMPRESSION zstd)""")
con.execute(f"""COPY (SELECT node::VARCHAR AS node, chain::VARCHAR AS chain, address::VARCHAR AS address, role::VARCHAR AS role,
  category::VARCHAR AS category, label::VARCHAR AS label, hop::INTEGER AS hop, received_usd::DOUBLE AS received_usd,
  held_usd::DOUBLE AS held_usd FROM read_json_auto('{n}', format='newline_delimited') ORDER BY received_usd DESC)
  TO '{out}/nodes.parquet' (FORMAT parquet, COMPRESSION zstd)""")
`;
const res = spawnSync("uv", ["run", "--quiet", "--no-project", "--with", "duckdb==1.4.3", "python", "-I", "-c", py, transfersFile, nodesFile, dataDir], {
  stdio: "inherit",
});
if (res.status !== 0) console.warn("Parquet files were not written (needs uv). The JSON fixtures are up to date.");

const shardFiles = readdirSync(join(OUT, "screen")).length;
console.log(
  `fixtures: ${graph.nodes.length} graph nodes, ${graph.edges.length} edges, ${transfers.length} transfers, ${feed.items.length} feed items, ${shardFiles} screen shards`,
);
console.log(
  `stolen ${(STOLEN_TOTAL / 1e9).toFixed(3)}B, frozen ${(frozenTotal / 1e9).toFixed(3)}B, held now ${(heldNowTotal / 1e9).toFixed(3)}B, exited ${(exitedTotal / 1e9).toFixed(3)}B`,
);
