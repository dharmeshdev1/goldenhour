import { isChain } from "@/lib/chains";
import type {
  CaseFile,
  Detector,
  Feed,
  FeedItem,
  FlowGraph,
  GoldenHour,
  GuardCases,
  Meta,
  Summary,
  TrailGraph,
} from "@/lib/types";

/**
 * Defensive readers for each document. The backend is the source of truth for the shape; these
 * only make sure a missing list or a stray null reads as "none" instead of crashing a page, and
 * drop rows whose chain is unknown (their explorer links and colors would be wrong).
 */

type Obj = Record<string, unknown>;

const obj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const arr = <T = unknown>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const num = (v: unknown, fallback = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const withChain = <T extends { chain?: unknown }>(rows: T[]): T[] => rows.filter((r) => r && isChain(r.chain));

export function normalizeCase(raw: unknown): CaseFile {
  const c = obj(raw);
  const reported = obj(c.reported);
  const window = obj(c.window);
  return {
    id: str(c.id),
    title: str(c.title),
    victim: str(c.victim),
    attribution: str(c.attribution),
    window: { start: str(window.start), end: str(window.end) },
    reported: {
      stolen_usd: num(reported.stolen_usd),
      stolen_usd_source: str(reported.stolen_usd_source),
      frozen_usd: num(reported.frozen_usd),
      frozen_usd_source: str(reported.frozen_usd_source),
      chains: num(reported.chains),
    },
    timeline: arr<Obj>(c.timeline)
      .map((t) => ({ at: str(obj(t).at), what: str(obj(t).what), source: str(obj(t).source) }))
      .filter((t) => t.at && t.what),
    sources: arr<Obj>(c.sources)
      .map((s) => ({ id: str(obj(s).id), title: str(obj(s).title), url: str(obj(s).url) }))
      .filter((s) => s.id),
  };
}

export function normalizeSummary(raw: unknown): Summary {
  const s = obj(raw);
  const stolen = obj(s.stolen);
  const now = obj(s.now);
  const held = obj(now.held);
  const frozen = obj(now.frozen);
  const exited = obj(now.exited);
  const toBtc = obj(obj(s.converted).to_btc);
  const trail = obj(s.trail);
  return {
    generated_at: str(s.generated_at),
    data_through: str(s.data_through),
    last_movement_at: typeof s.last_movement_at === "string" ? s.last_movement_at : null,
    stolen: {
      usd_at_theft: num(stolen.usd_at_theft),
      ...(typeof stolen.reported_usd === "number" && Number.isFinite(stolen.reported_usd) && stolen.reported_usd > 0
        ? { reported_usd: stolen.reported_usd }
        : {}),
      by_chain: withChain(arr(stolen.by_chain)),
      by_asset: withChain(arr(stolen.by_asset)),
      transfers: num(stolen.transfers),
      victim_wallets: num(stolen.victim_wallets),
      first_at: str(stolen.first_at),
      last_at: str(stolen.last_at),
    },
    now: {
      held: {
        usd: num(held.usd),
        addresses: num(held.addresses),
        by_chain: withChain(arr(held.by_chain)),
        by_asset: withChain(arr(held.by_asset)),
      },
      frozen: { usd: num(frozen.usd), by_issuer: withChain(arr(frozen.by_issuer)) },
      exited: {
        usd: num(exited.usd),
        by_category: arr<Obj>(exited.by_category).map((c) => ({
          category: str(c.category, "other") as Summary["now"]["exited"]["by_category"][number]["category"],
          usd: num(c.usd),
          count: num(c.count),
          entities: arr<Obj>(c.entities).map((e) => ({ name: str(e.name), usd: num(e.usd), count: num(e.count) })),
        })),
      },
    },
    converted: { to_btc: { btc: num(toBtc.btc), usd: num(toBtc.usd), swaps: num(toBtc.swaps) } },
    trail: {
      addresses: num(trail.addresses),
      chains: arr(trail.chains).filter(isChain),
      max_hop: num(trail.max_hop),
      transfers: num(trail.transfers),
      services_touched: num(trail.services_touched),
    },
  };
}

export function normalizeFlows(raw: unknown): FlowGraph {
  const f = obj(raw);
  return { nodes: arr(f.nodes), links: arr(f.links) };
}

const FEED_KINDS = new Set(["move", "swap", "bridge", "exit", "freeze", "unfreeze", "mix"]);

export function normalizeFeed(raw: unknown): Feed {
  const f = obj(raw);
  const items = withChain(arr<FeedItem>(f.items)).filter((i) => typeof i.ts === "string" && typeof i.tx === "string");
  return {
    generated_at: str(f.generated_at),
    items: items.map((i) => ({
      ...i,
      kind: FEED_KINDS.has(i.kind) ? i.kind : "move",
      from: str(i.from),
      from_label: str(i.from_label),
      to: str(i.to),
      to_label: str(i.to_label),
      symbol: str(i.symbol),
      amount: num(i.amount),
      usd: num(i.usd),
      note: str(i.note),
      explorer_url: str(i.explorer_url),
    })),
  };
}

export function normalizeGraph(raw: unknown): TrailGraph {
  const g = obj(raw);
  const nodes = withChain(arr<TrailGraph["nodes"][number]>(g.nodes)).filter((n) => typeof n.id === "string" && n.id);
  const ids = new Set<string>();
  const unique = nodes.filter((n) => (ids.has(n.id) ? false : (ids.add(n.id), true)));
  const edges = arr<TrailGraph["edges"][number]>(g.edges).filter(
    (e) => e && ids.has(e.source) && ids.has(e.target) && e.source !== e.target,
  );
  return {
    nodes: unique.map((n) => ({
      ...n,
      address: str(n.address),
      label: str(n.label),
      category: str(n.category),
      hop: num(n.hop),
      received_usd: num(n.received_usd),
      held_usd: num(n.held_usd),
      first_ts: num(n.first_ts),
    })),
    edges: edges.map((e) => ({
      ...e,
      usd: num(e.usd),
      count: num(e.count, 1),
      first_ts: num(e.first_ts),
      last_ts: num(e.last_ts),
      kind: e.kind === "bridge" ? "bridge" : "transfer",
    })),
    truncated: Boolean(g.truncated),
    total_nodes: num(g.total_nodes, unique.length),
    total_edges: num(g.total_edges, edges.length),
  };
}

export function normalizeGoldenHour(raw: unknown): GoldenHour {
  const g = obj(raw);
  return {
    t0: str(g.t0),
    minutes: arr<number>(g.minutes),
    drained_usd: arr<number>(g.drained_usd),
    freezable_usd: arr<number>(g.freezable_usd),
    escaped_usd: arr<number>(g.escaped_usd),
    events: arr<Obj>(g.events)
      .map((e) => ({
        minute: num(e.minute, NaN),
        at: str(e.at),
        label: str(e.label),
        kind: str(e.kind, "theft") as GoldenHour["events"][number]["kind"],
        source: str(e.source),
      }))
      .filter((e) => Number.isFinite(e.minute) && e.label),
    scenarios: arr(g.scenarios),
    notes: arr(g.notes).filter((n): n is string => typeof n === "string" && n.length > 0),
  };
}

export function normalizeDetector(raw: unknown): Detector {
  const d = obj(raw);
  const b = obj(d.baseline);
  return {
    replay: withChain(arr(d.replay)),
    baseline: {
      days: num(b.days),
      wallets: num(b.wallets),
      transactions: num(b.transactions),
      alerts: num(b.alerts),
      alerts_per_wallet_day: num(b.alerts_per_wallet_day),
    },
    rules: arr<Obj>(d.rules).map((r) => ({
      id: str(r.id),
      title: str(r.title),
      description: str(r.description),
      severity: (["critical", "high", "medium"].includes(str(r.severity)) ? r.severity : "medium") as Detector["rules"][number]["severity"],
    })),
    unread_blocks: num(d.unread_blocks),
  };
}

export function normalizeGuard(raw: unknown): GuardCases {
  const g = obj(raw);
  return {
    policy_yaml: str(g.policy_yaml),
    cases: withChain(arr<GuardCases["cases"][number]>(g.cases)).map((c) => ({
      ...c,
      id: str(c.id),
      title: str(c.title),
      story: str(c.story),
      tx: typeof c.tx === "string" ? c.tx : null,
      request: obj(c.request),
      decision: c.decision === "allow" || c.decision === "deny" || c.decision === "hold" ? c.decision : "hold",
      reasons: arr<Obj>(c.reasons).map((r) => ({ rule: str(r.rule), message: str(r.message) })),
    })),
  };
}

export function normalizeMeta(raw: unknown): Meta {
  const m = obj(raw);
  const t = obj(m.tick);
  const sources = obj(m.sources_ok);
  return {
    generated_at: str(m.generated_at),
    version: str(m.version),
    tick: {
      at: str(t.at),
      duration_s: num(t.duration_s),
      fetched: num(t.fetched),
      joined: num(t.joined),
      errors: num(t.errors),
    },
    sources_ok: Object.fromEntries(Object.entries(sources).filter(([, v]) => typeof v === "boolean")) as Record<string, boolean>,
  };
}
