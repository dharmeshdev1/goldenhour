"use client";

import { useSyncExternalStore } from "react";
import { CodeBlock } from "@/components/ui/code-block";
import { ExternalIcon } from "@/components/ui/external";
import { SectionHeader } from "@/components/ui/section";
import { API_BASE, apiUrl } from "@/lib/api";
import { POLL_MS } from "@/lib/use-api";

interface StaticDoc {
  path: string;
  what: string;
  cadence: string;
  shape: string;
  link?: boolean;
}

const DOCS: StaticDoc[] = [
  {
    path: "summary.json",
    what: "Totals: stolen at theft prices (with the reported figure when known), held now, frozen by issuer, exited by category, conversions to bitcoin and trail statistics.",
    cadence: "Every run",
    link: true,
    shape: `interface Summary {
  generated_at: string; data_through: string; last_movement_at: string | null;
  stolen: { usd_at_theft: number; reported_usd?: number; by_chain: { chain: Chain; usd: number }[];
    by_asset: { symbol: string; chain: Chain; amount: number; usd: number }[];
    transfers: number; victim_wallets: number; first_at: string; last_at: string };
  now: {
    held: { usd: number; addresses: number; by_chain: { chain: Chain; usd: number; addresses: number }[];
      by_asset: { symbol: string; chain: Chain; amount: number; usd: number }[] };
    frozen: { usd: number; by_issuer: { issuer: string; chain: Chain; symbol: string; amount: number;
      usd: number; addresses: number; first_at: string }[] };
    exited: { usd: number; by_category: { category: "exchange" | "mixer" | "bridge" | "lending" | "dex"
      | "staking" | "other"; usd: number; count: number; entities: { name: string; usd: number; count: number }[] }[] };
  };
  converted: { to_btc: { btc: number; usd: number; swaps: number } };
  trail: { addresses: number; chains: Chain[]; max_hop: number; transfers: number; services_touched: number };
}`,
  },
  {
    path: "feed.json",
    what: "The latest movements on the trail, newest first, up to 200.",
    cadence: "Every run",
    link: true,
    shape: `interface Feed {
  generated_at: string;
  items: { ts: string; chain: Chain; tx: string; explorer_url: string;
    kind: "move" | "swap" | "bridge" | "exit" | "freeze" | "unfreeze" | "mix";
    from: string; from_label: string; to: string; to_label: string;
    symbol: string; amount: number; usd: number; note: string }[];
}`,
  },
  {
    path: "flows.json",
    what: "Victim to chains to where the value is now, as Sankey nodes and links, at theft prices.",
    cadence: "Every run",
    link: true,
    shape: `interface FlowGraph {
  nodes: { id: string; label: string; kind: "victim" | "chain" | "state" | "category" }[];
  links: { source: string; target: string; usd: number }[];
}`,
  },
  {
    path: "graph.json",
    what: "The top addresses and edges of the trail by value. The full trail is in the Parquet files.",
    cadence: "Every run",
    link: true,
    shape: `interface TrailGraph {
  nodes: { id: string; chain: Chain; address: string;
    role: "victim" | "seed" | "trail" | "service" | "hub" | "counterparty";
    category: string; label: string; hop: number; received_usd: number; held_usd: number; first_ts: number }[];
  edges: { source: string; target: string; usd: number; count: number;
    first_ts: number; last_ts: number; kind: "transfer" | "bridge" }[];
  truncated: boolean; total_nodes: number; total_edges: number;
}`,
  },
  {
    path: "golden-hour.json",
    what: "The minute series of the theft (drained, freezable, escaped), its events, the response scenarios and their assumptions.",
    cadence: "When the model changes",
    link: true,
    shape: `interface GoldenHour {
  t0: string; minutes: number[]; drained_usd: number[]; freezable_usd: number[]; escaped_usd: number[];
  events: { minute: number; at: string; label: string; kind: "theft" | "freeze" | "swap" | "revoke" | "detect"; source: string }[];
  scenarios: { response_minutes: number; freezable_saved_usd: number; drain_prevented_usd: number }[];
  notes: string[];
}`,
  },
  {
    path: "detector.json",
    what: "Watch replayed over the victim's wallets, its false-alarm baseline and its rules.",
    cadence: "When the model changes",
    link: true,
    shape: `interface Detector {
  replay: { chain: Chain; wallet: string; label: string; first_theft_at: string; first_alert_at: string | null;
    minutes_after_first_theft: number | null; usd_before_alert: number; rule: string }[];
  baseline: { days: number; wallets: number; transactions: number; alerts: number; alerts_per_wallet_day: number };
  unread_blocks: number; // blocks the free explorer could not serve, skipped and not counted
  rules: { id: string; title: string; description: string; severity: "critical" | "high" | "medium" }[];
}`,
  },
  {
    path: "guard.json",
    what: "The Guard policy and the cases it was evaluated on.",
    cadence: "When the policy changes",
    link: true,
    shape: `interface GuardCases {
  policy_yaml: string;
  cases: { id: string; title: string; story: string; chain: Chain; tx: string | null;
    request: Record<string, unknown>; decision: "allow" | "deny" | "hold";
    reasons: { rule: string; message: string }[] }[];
}`,
  },
  {
    path: "case.json",
    what: "The case file: victim, attribution, time window, reported totals, timeline and sources.",
    cadence: "When the case changes",
    link: true,
    shape: `interface CaseFile {
  id: string; title: string; victim: string; attribution: string; window: { start: string; end: string };
  reported: { stolen_usd: number; stolen_usd_source: string; frozen_usd: number; frozen_usd_source: string; chains: number };
  timeline: { at: string; what: string; source: string }[]; // at: ISO, "YYYY-MM-DD" or "YYYY-MM"
  sources: { id: string; title: string; url: string }[];
}`,
  },
  {
    path: "meta.json",
    what: "When the pipeline last ran, how long it took, and which sources answered.",
    cadence: "Every run",
    link: true,
    shape: `interface Meta {
  generated_at: string; version: string;
  tick: { at: string; duration_s: number; fetched: number; joined: number; errors: number };
  sources_ok: Record<string, boolean>;
}`,
  },
  {
    path: "screen/{prefix}.json",
    what: "Screening shards. The prefix is the first three hex characters of sha256(address), with the address alone spelled canonically (EVM and bech32 lowercased, base58 as written) and no chain attached. Each key lists one entry per chain the address appears on. A missing shard has no entries.",
    cadence: "Every run",
    shape: `interface ScreenShard {
  v: 2;
  entries: Record<string /* sha256 hex of the address */, Array<{
    node: string; chain: Chain; address: string; role: string; category: string; label: string;
    hop: number; received_usd: number; held_usd: number; first_ts: number; reason: string;
    exposure: "trail" | "direct" }>>;
}`,
  },
  {
    path: "data/transfers.parquet",
    what: "Every transfer on the trail: uid, chain, tx, ts (Unix seconds), sender, receiver (node ids), symbol, amount, usd, kind.",
    cadence: "Every run",
    link: true,
    shape: "transfers(uid, chain, tx, ts, sender, receiver, symbol, amount, usd, kind)",
  },
  {
    path: "data/nodes.parquet",
    what: "Every address the trail judged: node, chain, address, role, category, label, hop, received_usd, held_usd.",
    cadence: "Every run",
    link: true,
    shape: "nodes(node, chain, address, role, category, label, hop, received_usd, held_usd)",
  },
];

const REST: { method: string; path: string; what: string; request: string; response: string }[] = [
  {
    method: "GET",
    path: "/v1/screen/{chain}/{address}",
    what: "Screen one address, on one chain or on every EVM chain with chain=evm. The server normalizes it the same way as the static shards. 200 for any valid address, with found saying whether it is on or paid by the trail; 422 for an address that is not valid on the chain.",
    request: "curl -s https://goldenhour.internal.example/v1/screen/tron/<address>",
    response: `{
  "checked": ["tron:<address>"],
  "found": true,
  "matches": [{
    "node": "tron:<address>",
    "chain": "tron",
    "role": "trail",
    "exposure": "trail",
    "hop": 4,
    "received_usd": 1840000,
    "held_usd": 120000,
    "reason": "91% of the value it ever received came from trail addresses (hop 4)."
  }],
  "note": "A miss means the address is not on this trail. It does not mean the address is clean."
}`,
  },
  {
    method: "GET",
    path: "/v1/cases/{id}/summary",
    what: "The same document as summary.json, for any case the server tracks, for example binance-2026-10.",
    request: "curl -s https://goldenhour.internal.example/v1/cases/binance-2026-10/summary",
    response: `{
  "generated_at": "…",
  "data_through": "…",
  "last_movement_at": "…",
  "stolen": { "usd_at_theft": …, "reported_usd": …, "by_chain": [ … ], … },
  "now": { "held": { … }, "frozen": { … }, "exited": { … } },
  "converted": { "to_btc": { … } },
  "trail": { … }
}`,
  },
  {
    method: "POST",
    path: "/v1/guard/evaluate",
    what: "Evaluate a signing request against the Guard policy before it reaches the signer. The body is the decoded request, as in guard.json; the answer is the decision and the rules that produced it.",
    request: `curl -s -X POST https://goldenhour.internal.example/v1/guard/evaluate \\
  -H 'content-type: application/json' \\
  -d '{"type": "eip7702_authorization", "wallet": "hot-eth-04", "chain": "ethereum",
       "delegate": "0x0000000000000000000000000000000000000000"}'`,
    response: `{
  "decision": "deny",
  "reasons": [
    { "rule": "no-delegation", "message": "Custody wallets never delegate their code." }
  ]
}`,
  },
  {
    method: "GET",
    path: "/v1/stream",
    what: "Server-Sent Events: a movement event for each new transfer on a trail and a summary event after each run, so a response team can watch without polling.",
    request: "curl -N https://goldenhour.internal.example/v1/stream",
    response: `event: movement
data: {"ts": "…", "chain": "bitcoin", "kind": "mix", "usd": …, "note": "Entered a coinjoin coordinator", …}

event: summary
data: {"generated_at": "…", "now": {"held": {"usd": …}, …}, …}`,
  },
];

export function ApiDocs() {
  const origin = useSyncExternalStore(
    () => () => {},
    () => window.location.origin,
    () => "",
  );
  const base = /^https?:/i.test(API_BASE) ? API_BASE : `${origin}${API_BASE}`;
  return (
    <>
      <section className="shell" aria-labelledby="static-title">
        <SectionHeader
          id="static-title"
          eyebrow="Static JSON"
          title="Published files"
          lead={
            <p>
              The pipeline republishes these files every few minutes, next to this site. They need no key. This site reads them in your browser
              and refreshes the live ones every {Math.round(POLL_MS / 1000)} seconds.
            </p>
          }
        />
        <div className="card mb-6 flex flex-wrap items-center gap-3 p-4 text-[13.5px]">
          <span className="eyebrow">Base URL</span>
          <code className="mono break-all text-gold-2">{base || API_BASE}</code>
        </div>
        <div className="space-y-3">
          {DOCS.map((d) => (
            <details key={d.path} className="card group">
              <summary className="flex flex-col gap-2 p-5 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <p className="mono text-[14px] text-ink">
                    <span className="mr-2 rounded bg-[rgb(107_214_160/0.12)] px-1.5 py-0.5 text-[11px] text-green">GET</span>
                    {d.path}
                  </p>
                  <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-3">{d.what}</p>
                </div>
                <div className="flex shrink-0 items-center gap-3 text-[12px] text-ink-3">
                  <span>{d.cadence}</span>
                  {d.link ? (
                    <a href={apiUrl(d.path)} target="_blank" rel="noopener noreferrer" className="chip no-underline hover:text-ink" onClick={(e) => e.stopPropagation()}>
                      {d.path.endsWith(".parquet") ? "Download" : "Open"} <ExternalIcon />
                    </a>
                  ) : null}
                  <span className="chip group-open:border-gold-3 group-open:text-gold-2">Shape</span>
                </div>
              </summary>
              <div className="border-t border-line p-4">
                <CodeBlock code={d.shape} label={d.path.endsWith(".parquet") ? "columns" : "TypeScript"} />
              </div>
            </details>
          ))}
        </div>
        <p className="mt-4 text-[12.5px] text-ink-3">
          Chain is one of ethereum, optimism, arbitrum, base, polygon, avalanche, bitcoin, tron, xrpl, solana, near. Times are ISO 8601 in UTC
          unless named _ts, which are Unix seconds. Fields are only ever added within v1.
        </p>
      </section>

      <section className="shell mt-24" aria-labelledby="rest-title">
        <SectionHeader
          id="rest-title"
          eyebrow="Self-hosted"
          title="The REST API"
          lead={
            <p>
              Run the Goldenhour server inside your own network for screening at deposit time, policy checks in front of a signer, and a live
              stream for the response room. Requests and responses use the same shapes as the static files.
            </p>
          }
        />
        <div className="space-y-5">
          {REST.map((r) => (
            <article key={r.path} className="card overflow-hidden" aria-labelledby={`rest-${r.path}`}>
              <div className="p-5">
                <h3 id={`rest-${r.path}`} className="mono text-[15px] text-ink">
                  <span className={`mr-2 rounded px-1.5 py-0.5 text-[11px] ${r.method === "POST" ? "bg-[rgb(242_179_61/0.14)] text-gold-2" : "bg-[rgb(107_214_160/0.12)] text-green"}`}>{r.method}</span>
                  {r.path}
                </h3>
                <p className="mt-2 max-w-3xl text-[13.5px] leading-relaxed text-ink-3">{r.what}</p>
              </div>
              <div className="grid gap-px border-t border-line bg-line lg:grid-cols-2">
                <div className="min-w-0 bg-bg-1 p-4">
                  <CodeBlock code={r.request} label="Request" />
                </div>
                <div className="min-w-0 bg-bg-1 p-4">
                  <CodeBlock code={r.response} label="Response" lang={r.response.trim().startsWith("{") ? "json" : "plain"} />
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
