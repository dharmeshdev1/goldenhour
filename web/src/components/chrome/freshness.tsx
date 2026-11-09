"use client";

import { useEffect, useState } from "react";
import { formatRelative, formatUtc, parseTime } from "@/lib/format";
import { useMeta } from "@/lib/use-api";

export type FreshnessLevel = "live" | "delayed" | "stale" | "offline" | "loading";

/** The pipeline republishes every 5 minutes; anything much older than that is called out. */
export function freshnessLevel(generatedAt: string | null | undefined, now: number, failed: boolean): FreshnessLevel {
  const t = parseTime(generatedAt);
  if (t === null) return failed ? "offline" : "loading";
  const age = now - t;
  if (age <= 20 * 60_000) return "live";
  if (age <= 3 * 3_600_000) return "delayed";
  return "stale";
}

const TEXT: Record<FreshnessLevel, string> = {
  live: "Live",
  delayed: "Delayed",
  stale: "Stale",
  offline: "No data",
  loading: "Loading",
};

const DOT: Record<FreshnessLevel, string> = {
  live: "bg-gold text-gold animate-pulse-dot",
  delayed: "bg-ember text-ember",
  stale: "bg-red text-red",
  offline: "bg-ink-4 text-ink-4",
  loading: "bg-ink-4 text-ink-4",
};

/** Re-render on an interval so relative times stay true. */
export function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(id);
  }, [everyMs]);
  return now;
}

export function FreshnessPill({ compact = false }: { compact?: boolean }) {
  const meta = useMeta();
  const now = useNow();
  const level = freshnessLevel(meta.data?.generated_at, now, Boolean(meta.error));
  const at = meta.data?.generated_at;
  const label = at ? `Data published ${formatRelative(at, now)} (${formatUtc(at)})` : TEXT[level];
  return (
    <span className="chip h-7 gap-2 pr-3" title={label} aria-label={label}>
      <span aria-hidden="true" className={`inline-block size-1.5 rounded-full ${DOT[level]}`} />
      <span className="font-medium text-ink">{TEXT[level]}</span>
      {!compact && at ? <span className="num text-[11.5px] text-ink-3">{formatRelative(at, now)}</span> : null}
    </span>
  );
}
