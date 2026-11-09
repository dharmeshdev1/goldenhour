"use client";

import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/states";
import { Ticker } from "@/components/ui/ticker";
import { formatCount, formatPercent, formatUsd } from "@/lib/format";
import { EXIT_LABEL } from "@/lib/tones";
import { useCase, useSummary } from "@/lib/use-api";

function Stat({
  label,
  tone,
  value,
  children,
  loading,
}: {
  label: string;
  tone: string;
  value: number | undefined;
  children?: ReactNode;
  loading: boolean;
}) {
  return (
    <div className="relative flex flex-col bg-bg-1 p-6 sm:p-7">
      <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[2px]" style={{ background: `linear-gradient(90deg, ${tone}, transparent 85%)` }} />
      <p className="eyebrow flex items-center gap-2">
        <span aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: tone }} />
        {label}
      </p>
      {loading ? (
        <Skeleton className="mt-4 h-11 w-40" />
      ) : (
        <p className="num mt-4 text-[38px] leading-none text-ink sm:text-[44px]">
          <Ticker value={value} format={formatUsd} />
        </p>
      )}
      <div className="mt-4 flex-1 text-[13.5px] leading-relaxed text-ink-3">{children}</div>
    </div>
  );
}

export function BigNumbers() {
  const summary = useSummary();
  const kase = useCase();
  const s = summary.data;
  const c = kase.data;
  const loading = !s;
  const reported = s?.stolen.reported_usd;
  const verifiedShare = s && reported ? s.stolen.usd_at_theft / reported : null;
  const issuers = s ? Array.from(new Set(s.now.frozen.by_issuer.map((f) => f.issuer))) : [];
  const frozenAddresses = s ? s.now.frozen.by_issuer.reduce((n, f) => n + f.addresses, 0) : 0;
  const heldChains = s ? s.now.held.by_chain.filter((r) => r.usd > 0).length : 0;
  const categories = s ? [...s.now.exited.by_category].sort((a, b) => b.usd - a.usd) : [];

  return (
    <section aria-label="The theft in four numbers" className="shell relative z-10 -mt-px">
      <div className="grid gap-px overflow-hidden rounded-b-2xl border border-t-0 border-line bg-line sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Stolen" tone="var(--color-red)" value={s?.stolen.usd_at_theft} loading={loading}>
          {s ? (
            <>
              <p>Verified on-chain so far, at prices at the time of the theft.</p>
              {reported ? (
                <div className="mt-3">
                  <div className="h-1 overflow-hidden rounded-full bg-bg-3" aria-hidden="true">
                    <div className="h-full rounded-full bg-red/70" style={{ width: `${Math.min(100, (verifiedShare ?? 0) * 100)}%` }} />
                  </div>
                  <p className="mt-2 text-[12.5px]">
                    {verifiedShare !== null && verifiedShare <= 1
                      ? `${formatPercent(verifiedShare, 1)} of the ${formatUsd(reported)} reported`
                      : `Above the ${formatUsd(reported)} reported, by ${formatPercent((verifiedShare ?? 1) - 1, 1)}`}
                    {c?.reported.stolen_usd_source && Math.abs(c.reported.stolen_usd - reported) < 1 ? (
                      <span className="block text-ink-3">Reported: {c.reported.stolen_usd_source}</span>
                    ) : null}
                  </p>
                </div>
              ) : (
                <p className="mt-2 text-[12.5px]">{formatCount(s.stolen.transfers)} transfers from {formatCount(s.stolen.victim_wallets)} wallets.</p>
              )}
            </>
          ) : null}
        </Stat>
        <Stat label="Frozen" tone="var(--color-ice)" value={s?.now.frozen.usd} loading={loading}>
          {s ? (
            <>
              <p>
                Frozen by {issuers.length} {issuers.length === 1 ? "issuer" : "issuers"} at {formatCount(frozenAddresses)} addresses
                {issuers.length ? `: ${issuers.slice(0, 4).join(", ")}${issuers.length > 4 ? " and others" : ""}.` : "."}
              </p>
              {c?.reported.frozen_usd ? (
                <p className="mt-2 text-[12.5px]">
                  Reported frozen or seized: {formatUsd(c.reported.frozen_usd)}
                  <span className="block text-ink-3">{c.reported.frozen_usd_source}</span>
                </p>
              ) : null}
            </>
          ) : null}
        </Stat>
        <Stat label="Still held" tone="var(--color-ember)" value={s?.now.held.usd} loading={loading}>
          {s ? (
            <p>
              Sitting at {formatCount(s.now.held.addresses)} trail addresses on {heldChains} chains, valued at today&apos;s prices. This is
              what can still be frozen or seized.
            </p>
          ) : null}
        </Stat>
        <Stat label="Moved out" tone="var(--color-slate)" value={s?.now.exited.usd} loading={loading}>
          {s ? (
            <>
              <p>Reached services where the trail stops: bridges, mixers, exchanges and others.</p>
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {categories.slice(0, 4).map((c) => (
                  <li key={c.category} className="chip h-6 text-[11.5px]">
                    {EXIT_LABEL[c.category] ?? c.category} <span className="num text-ink-3">{formatUsd(c.usd)}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </Stat>
      </div>
    </section>
  );
}
