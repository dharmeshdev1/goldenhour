"use client";

import { useNow } from "@/components/chrome/freshness";
import { formatRelative, formatUtc, parseTime } from "@/lib/format";

export function RelativeTime({ value, className }: { value: string | number | null | undefined; className?: string }) {
  const now = useNow();
  const t = parseTime(value);
  if (t === null) return <span className={className}>n/a</span>;
  const iso = new Date(t).toISOString();
  return (
    <time dateTime={iso} title={formatUtc(t, true)} className={className}>
      {formatRelative(t, now)}
    </time>
  );
}
