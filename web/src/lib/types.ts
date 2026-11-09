/**
 * The data contract between the pipeline and this site. Every document lives under the API base
 * (`${basePath}/api/v1` by default) and is republished by the pipeline every few minutes, without
 * rebuilding the site. Keep these types in step with the backend; fields are only ever added.
 */

export type Chain =
  | "ethereum"
  | "optimism"
  | "arbitrum"
  | "base"
  | "polygon"
  | "avalanche"
  | "bitcoin"
  | "tron"
  | "xrpl"
  | "solana"
  | "near";

/** api/v1/case.json */
export interface CaseFile {
  id: string;
  title: string;
  victim: string;
  attribution: string;
  window: { start: string; end: string };
  reported: {
    stolen_usd: number;
    stolen_usd_source: string;
    frozen_usd: number;
    frozen_usd_source: string;
    chains: number;
  };
  /** `at` is an ISO timestamp, "YYYY-MM-DD" or "YYYY-MM". */
  timeline: { at: string; what: string; source: string }[];
  sources: { id: string; title: string; url: string }[];
}

export type ExitCategory = "exchange" | "mixer" | "bridge" | "lending" | "dex" | "staking" | "other";

/** api/v1/summary.json */
export interface Summary {
  generated_at: string;
  data_through: string;
  last_movement_at: string | null;
  stolen: {
    /** What Goldenhour has verified on-chain so far, at prices at the time of the theft. */
    usd_at_theft: number;
    /** The publicly reported total, when the pipeline carries one. Shown next to the verified figure. */
    reported_usd?: number;
    by_chain: { chain: Chain; usd: number }[];
    by_asset: { symbol: string; chain: Chain; amount: number; usd: number }[];
    transfers: number;
    victim_wallets: number;
    first_at: string;
    last_at: string;
  };
  now: {
    held: {
      usd: number;
      addresses: number;
      by_chain: { chain: Chain; usd: number; addresses: number }[];
      by_asset: { symbol: string; chain: Chain; amount: number; usd: number }[];
    };
    frozen: {
      usd: number;
      by_issuer: { issuer: string; chain: Chain; symbol: string; amount: number; usd: number; addresses: number; first_at: string }[];
    };
    exited: {
      usd: number;
      by_category: { category: ExitCategory; usd: number; count: number; entities: { name: string; usd: number; count: number }[] }[];
    };
  };
  converted: { to_btc: { btc: number; usd: number; swaps: number } };
  trail: { addresses: number; chains: Chain[]; max_hop: number; transfers: number; services_touched: number };
}

export type FlowNodeKind = "victim" | "chain" | "state" | "category";

/** api/v1/flows.json (Sankey: victim to chains to where the value is now) */
export interface FlowGraph {
  nodes: { id: string; label: string; kind: FlowNodeKind }[];
  links: { source: string; target: string; usd: number }[];
}

export type FeedKind = "move" | "swap" | "bridge" | "exit" | "freeze" | "unfreeze" | "mix";

/** api/v1/feed.json, newest first, up to 200 items */
export interface FeedItem {
  ts: string;
  chain: Chain;
  tx: string;
  explorer_url: string;
  kind: FeedKind;
  from: string;
  from_label: string;
  to: string;
  to_label: string;
  symbol: string;
  amount: number;
  usd: number;
  note: string;
}

export interface Feed {
  generated_at: string;
  items: FeedItem[];
}

export type NodeRole = "victim" | "seed" | "trail" | "service" | "hub" | "counterparty";

export interface TrailNode {
  id: string;
  chain: Chain;
  address: string;
  role: NodeRole;
  category: string;
  label: string;
  hop: number;
  received_usd: number;
  held_usd: number;
  /** Unix seconds. */
  first_ts: number;
}

export interface TrailEdge {
  source: string;
  target: string;
  usd: number;
  count: number;
  /** Unix seconds. */
  first_ts: number;
  last_ts: number;
  kind: "transfer" | "bridge";
}

/** api/v1/graph.json (the top nodes by value; the full data is in the Parquet files) */
export interface TrailGraph {
  nodes: TrailNode[];
  edges: TrailEdge[];
  truncated: boolean;
  total_nodes: number;
  total_edges: number;
}

export type GoldenEventKind = "theft" | "freeze" | "swap" | "revoke" | "detect";

/** api/v1/golden-hour.json */
export interface GoldenHour {
  t0: string;
  minutes: number[];
  drained_usd: number[];
  freezable_usd: number[];
  escaped_usd: number[];
  events: { minute: number; at: string; label: string; kind: GoldenEventKind; source: string }[];
  scenarios: { response_minutes: number; freezable_saved_usd: number; drain_prevented_usd: number }[];
  notes: string[];
}

export type Severity = "critical" | "high" | "medium";

/** api/v1/detector.json */
export interface Detector {
  replay: {
    chain: Chain;
    wallet: string;
    label: string;
    first_theft_at: string;
    first_alert_at: string | null;
    minutes_after_first_theft: number | null;
    usd_before_alert: number;
    rule: string;
  }[];
  baseline: { days: number; wallets: number; transactions: number; alerts: number; alerts_per_wallet_day: number };
  rules: { id: string; title: string; description: string; severity: Severity }[];
  /** Blocks of the replayed wallets the free explorer could not serve, skipped and not counted. */
  unread_blocks: number;
}

export type GuardDecision = "allow" | "deny" | "hold";

export interface GuardCase {
  id: string;
  title: string;
  story: string;
  chain: Chain;
  tx: string | null;
  request: Record<string, unknown>;
  decision: GuardDecision;
  reasons: { rule: string; message: string }[];
}

/** api/v1/guard.json */
export interface GuardCases {
  policy_yaml: string;
  cases: GuardCase[];
}

/** api/v1/meta.json */
export interface Meta {
  generated_at: string;
  version: string;
  tick: { at: string; duration_s: number; fetched: number; joined: number; errors: number };
  sources_ok: Record<string, boolean>;
}

export type Exposure = "trail" | "direct";

export interface ScreenEntry {
  node: string;
  chain: Chain;
  address: string;
  role: string;
  category: string;
  label: string;
  hop: number;
  received_usd: number;
  held_usd: number;
  first_ts: number;
  reason: string;
  exposure: Exposure;
}

/** api/v1/screen/{prefix}.json: k-anonymous screening shards keyed by the full sha256 hex. */
/** One screening shard. Keys are sha256 of the bare address; each lists one entry per chain. */
export interface ScreenShard {
  v: 2;
  entries: Record<string, ScreenEntry[]>;
}
