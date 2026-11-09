"use client";

import { useEffect, useMemo, useState } from "react";
import { ChainChip } from "@/components/ui/chain-chip";
import { CopyButton } from "@/components/ui/copy-button";
import { ExternalIcon } from "@/components/ui/external";
import { loadShard } from "@/lib/api";
import { chainName, isTxNode, nodeUrl } from "@/lib/chains";
import { addressLabel, formatCount, formatUsd, formatUtc, shortAddress } from "@/lib/format";
import { addressKey, entriesAt, shardPrefix } from "@/lib/screen";
import type { TrailEdge, TrailNode } from "@/lib/types";
import { ROLE_INFO } from "@/components/trail/trail-style";

/** Why an address is on the graph, in words, when the screening shard has no reason for it. */
function fallbackReason(n: TrailNode): string {
  switch (n.role) {
    case "victim":
      return "A wallet of the victim that sent value to the attacker during the theft window.";
    case "seed":
      return "A starting point of the trail: named in a published attribution, or a first hop out of a victim wallet.";
    case "trail":
      return "Joined the trail because most of the value it ever received came from trail addresses.";
    case "hub":
      return "A bitcoin transaction. Its outputs carry the share of its inputs that came from the trail.";
    case "service":
      return "A service such as an exchange, bridge or mixer. Value that reaches it has left the attacker's direct control, so the walk stops here.";
    case "counterparty":
      return "Received value from the trail, but most of its value came from elsewhere, so it did not join the trail.";
    default:
      return "";
  }
}

function useReason(node: TrailNode | null): { reason: string | null; loading: boolean } {
  const [state, setState] = useState<{ id: string | null; reason: string | null }>({ id: null, reason: null });
  useEffect(() => {
    if (!node || node.role === "hub" || node.role === "victim") return;
    let cancelled = false;
    (async () => {
      try {
        const key = await addressKey(node.address);
        const shard = await loadShard(shardPrefix(key));
        const entry = entriesAt(shard, key).find((e) => e.node === node.id);
        if (!cancelled) setState({ id: node.id, reason: entry?.reason || null });
      } catch {
        if (!cancelled) setState({ id: node.id, reason: null });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [node]);
  if (!node || node.role === "hub" || node.role === "victim") return { reason: null, loading: false };
  return { reason: state.id === node.id ? state.reason : null, loading: state.id !== node.id };
}

export function NodePanel({
  node,
  nodes,
  edges,
  onSelect,
  onClose,
}: {
  node: TrailNode;
  nodes: Map<string, TrailNode>;
  edges: TrailEdge[];
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const { reason, loading } = useReason(node);
  const url = nodeUrl(node);
  const incoming = useMemo(() => edges.filter((e) => e.target === node.id).sort((a, b) => b.usd - a.usd), [edges, node.id]);
  const outgoing = useMemo(() => edges.filter((e) => e.source === node.id).sort((a, b) => b.usd - a.usd), [edges, node.id]);
  const title = isTxNode(node.id) ? `Transaction ${shortAddress(node.address, 8, 6)}` : addressLabel(node.label, node.address);

  const list = (rows: TrailEdge[], dir: "in" | "out") =>
    rows.slice(0, 5).map((e) => {
      const other = nodes.get(dir === "in" ? e.source : e.target);
      if (!other) return null;
      return (
        <li key={`${e.source}>${e.target}`}>
          <button
            type="button"
            onClick={() => onSelect(other.id)}
            className="flex w-full items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-left text-[12.5px] hover:bg-[rgb(255_236_210/0.05)]"
          >
            <span className="min-w-0 truncate text-ink-2">
              {e.kind === "bridge" ? <span className="mr-1 text-gold-2">bridge</span> : null}
              {isTxNode(other.id) ? `tx ${shortAddress(other.address, 6, 4)}` : addressLabel(other.label, other.address)}
            </span>
            <span className="num shrink-0 text-ink-3">
              {formatUsd(e.usd)}
              {e.count > 1 ? ` · ${formatCount(e.count)}x` : ""}
            </span>
          </button>
        </li>
      );
    });

  return (
    <aside className="card flex max-h-full flex-col overflow-hidden" aria-labelledby="node-title">
      <div className="flex items-start justify-between gap-3 border-b border-line p-5">
        <div className="min-w-0">
          <p className="eyebrow" style={{ color: ROLE_INFO[node.role]?.color }}>
            {ROLE_INFO[node.role]?.label ?? node.role}
          </p>
          <h3 id="node-title" className="mt-1.5 truncate text-[17px] font-semibold text-ink">
            {title}
          </h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <ChainChip chain={node.chain} />
            {node.category ? <span className="chip">{node.category}</span> : null}
            <span className="chip num">hop {node.hop}</span>
          </div>
        </div>
        <button type="button" onClick={onClose} className="btn btn-ghost btn-small shrink-0" aria-label="Close details">
          Close
        </button>
      </div>
      <div className="overflow-y-auto p-5">
        <p className="mono break-all text-[12px] leading-relaxed text-ink-2">{node.address}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <CopyButton text={node.address} label="Copy address" />
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] text-ink-3 no-underline hover:bg-[rgb(255_236_210/0.06)] hover:text-ink">
              Open on {chainName(node.chain)} explorer <ExternalIcon />
            </a>
          ) : null}
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-3">
          <div className="card-quiet p-3">
            <dt className="eyebrow text-[10px]">{node.role === "victim" ? "Sent to the trail" : "Received"}</dt>
            <dd className="num mt-1 text-[18px] text-ink">{formatUsd(node.received_usd)}</dd>
          </div>
          <div className="card-quiet p-3">
            <dt className="eyebrow text-[10px]">Held now</dt>
            <dd className="num mt-1 text-[18px] text-ember">{formatUsd(node.held_usd)}</dd>
          </div>
        </dl>
        <p className="mt-3 text-[12px] text-ink-3">First seen {node.first_ts ? formatUtc(node.first_ts * 1000) : "n/a"}</p>
        <div className="mt-5">
          <p className="eyebrow mb-2">Why it is here</p>
          <p className="text-[13.5px] leading-relaxed text-ink-2">{loading ? "Looking up the screening record" : reason ?? fallbackReason(node)}</p>
        </div>
        {incoming.length ? (
          <div className="mt-5">
            <p className="eyebrow mb-1">Largest inflows</p>
            <ul>{list(incoming, "in")}</ul>
          </div>
        ) : null}
        {outgoing.length ? (
          <div className="mt-4">
            <p className="eyebrow mb-1">Largest outflows</p>
            <ul>{list(outgoing, "out")}</ul>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
