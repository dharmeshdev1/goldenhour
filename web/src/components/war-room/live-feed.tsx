"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ChainDot } from "@/components/ui/chain-chip";
import { ExternalIcon } from "@/components/ui/external";
import { KindBadge, kindLabel } from "@/components/ui/kind-badge";
import { SectionHeader } from "@/components/ui/section";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { RelativeTime } from "@/components/ui/time";
import { chainName, trustedExplorerUrl, txUrl } from "@/lib/chains";
import { addressLabel, formatAmount, formatClock, formatCount, formatUsd, formatUtc } from "@/lib/format";
import type { FeedItem, FeedKind, Summary } from "@/lib/types";
import { POLL_MS, useFeed, useSummary } from "@/lib/use-api";

const itemKey = (i: FeedItem) => `${i.chain}:${i.tx}:${i.to}:${i.ts}`;
const KINDS: (FeedKind | "all")[] = ["all", "move", "swap", "bridge", "exit", "mix", "freeze"];

function Row({ item, fresh }: { item: FeedItem; fresh: boolean }) {
  const href = trustedExplorerUrl(item.explorer_url) ?? txUrl(item.chain, item.tx);
  return (
    <motion.li
      layout
      initial={{ opacity: 0, y: -14, backgroundColor: "rgba(242,179,61,0.10)" }}
      animate={{ opacity: 1, y: 0, backgroundColor: fresh ? "rgba(242,179,61,0.06)" : "rgba(242,179,61,0)" }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
      className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 border-b border-line px-4 py-3.5 last:border-b-0 sm:grid-cols-[86px_74px_minmax(0,1fr)_auto] sm:items-center sm:px-5"
    >
      <div className="order-3 col-span-2 flex items-center gap-2 text-[12px] text-ink-3 sm:order-none sm:col-span-1 sm:block">
        <RelativeTime value={item.ts} className="num block text-ink-2" />
        <span className="num text-[11px] text-ink-3">{formatClock(item.ts)}</span>
      </div>
      <div className="order-1 sm:order-none">
        <KindBadge kind={item.kind} />
      </div>
      <div className="order-2 col-span-2 min-w-0 sm:order-none sm:col-span-1">
        <p className="truncate text-[13.5px] text-ink">{item.note || kindLabel(item.kind)}</p>
        <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12px] text-ink-3">
          <ChainDot chain={item.chain} size={6} />
          <span className="sr-only">{chainName(item.chain)}:</span>
          <span className="truncate" title={item.from}>
            {addressLabel(item.from_label, item.from)}
          </span>
          <span aria-hidden="true">→</span>
          <span className="sr-only">to</span>
          <span className="truncate" title={item.to}>
            {addressLabel(item.to_label, item.to)}
          </span>
        </p>
      </div>
      <div className="order-1 text-right sm:order-none">
        <p className="num text-[13.5px] text-ink">{formatUsd(item.usd)}</p>
        <p className="num flex items-center justify-end gap-1.5 text-[11.5px] text-ink-3">
          {formatAmount(item.amount, item.symbol)}
          {href ? (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-ink-3 hover:text-gold-2" aria-label={`Transaction on ${chainName(item.chain)} explorer`} title="Open in explorer">
              <ExternalIcon />
            </a>
          ) : null}
        </p>
      </div>
    </motion.li>
  );
}

function Glance({ s }: { s: Summary }) {
  const rows: [string, string][] = [
    ["Addresses on the trail", formatCount(s.trail.addresses)],
    ["Chains followed", formatCount(s.trail.chains.length)],
    ["Deepest hop", formatCount(s.trail.max_hop)],
    ["Transfers followed", formatCount(s.trail.transfers)],
    ["Services reached", formatCount(s.trail.services_touched)],
    ["Converted to bitcoin", `${formatAmount(s.converted.to_btc.btc, "BTC")}`],
  ];
  return (
    <aside className="card p-6" aria-labelledby="glance">
      <h3 id="glance" className="text-[15px] font-semibold text-ink">
        The trail at a glance
      </h3>
      <dl className="mt-4 divide-y divide-line">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-4 py-2.5 text-[13px]">
            <dt className="text-ink-3">{k}</dt>
            <dd className="num text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-[12.5px] leading-relaxed text-ink-3">
        {formatCount(s.converted.to_btc.swaps)} cross-chain swaps turned {formatUsd(s.converted.to_btc.usd)} of other assets into bitcoin.
      </p>
    </aside>
  );
}

export function LiveFeed() {
  const feed = useFeed();
  const summary = useSummary();
  const [kind, setKind] = useState<FeedKind | "all">("all");
  const [limit, setLimit] = useState(12);
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [announce, setAnnounce] = useState("");

  const items = useMemo(() => feed.data?.items ?? [], [feed.data]);
  useEffect(() => {
    if (!items.length) return;
    const keys = items.map(itemKey);
    if (seen.current === null) {
      seen.current = new Set(keys);
      return;
    }
    const added = keys.filter((k) => !(seen.current as Set<string>).has(k));
    for (const k of keys) (seen.current as Set<string>).add(k);
    if (!added.length) return;
    const raf = requestAnimationFrame(() => {
      setFresh(new Set(added));
      setAnnounce(`${added.length} new ${added.length === 1 ? "movement" : "movements"} on the trail`);
    });
    return () => cancelAnimationFrame(raf);
  }, [items]);

  const shown = items.filter((i) => kind === "all" || i.kind === kind);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of items) m.set(i.kind, (m.get(i.kind) ?? 0) + 1);
    return m;
  }, [items]);
  const s = summary.data;

  return (
    <section className="shell mt-28" aria-labelledby="feed-title">
      <SectionHeader
        id="feed-title"
        eyebrow="Live movements"
        title="What the stolen money is doing now"
        lead={
          <p>
            New transfers on the trail, newest first. The pipeline republishes every few minutes and this page checks every{" "}
            {Math.round(POLL_MS / 1000)} seconds.
          </p>
        }
        aside={
          s ? (
            <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-1 text-[12.5px]">
              <dt className="text-ink-3">Last movement</dt>
              <dd className="num text-ink-2">{s.last_movement_at ? <RelativeTime value={s.last_movement_at} /> : "none"}</dd>
              <dt className="text-ink-3">Data through</dt>
              <dd className="num text-ink-2" title={formatUtc(s.data_through)}>
                {formatClock(s.data_through)}
              </dd>
            </dl>
          ) : null
        }
      />
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="card overflow-hidden">
          <div className="flex flex-wrap gap-1.5 border-b border-line px-4 py-3 sm:px-5" role="group" aria-label="Filter movements by kind">
            {KINDS.map((k) => {
              const n = k === "all" ? items.length : counts.get(k) ?? 0;
              if (k !== "all" && n === 0) return null;
              return (
                <button
                  key={k}
                  type="button"
                  aria-pressed={kind === k}
                  onClick={() => setKind(k)}
                  className={`chip h-7 cursor-pointer transition-colors ${kind === k ? "border-gold-3 bg-[rgb(242_179_61/0.1)] text-gold-2" : "hover:text-ink"}`}
                >
                  {k === "all" ? "All" : kindLabel(k)}
                  <span className="num text-[11px] text-ink-3">{n}</span>
                </button>
              );
            })}
          </div>
          {feed.error && !feed.data ? (
            <div className="p-5">
              <ErrorState error={feed.error} onRetry={feed.reload} what="The movement feed" />
            </div>
          ) : !feed.data ? (
            <div className="space-y-3 p-5">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : shown.length === 0 ? (
            <p className="p-6 text-[14px] text-ink-3">No movements of this kind in the latest feed.</p>
          ) : (
            <ol aria-label="Movements on the trail, newest first">
              <AnimatePresence initial={false}>
                {shown.slice(0, limit).map((item) => (
                  <Row key={itemKey(item)} item={item} fresh={fresh.has(itemKey(item))} />
                ))}
              </AnimatePresence>
            </ol>
          )}
          {shown.length > limit ? (
            <div className="border-t border-line p-3 text-center">
              <button type="button" className="btn btn-ghost btn-small" onClick={() => setLimit((l) => l + 20)}>
                Show {Math.min(20, shown.length - limit)} more
              </button>
            </div>
          ) : null}
        </div>
        {s ? <Glance s={s} /> : <Skeleton className="h-80 w-full" />}
      </div>
    </section>
  );
}
