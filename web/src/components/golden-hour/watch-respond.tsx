"use client";

import { ChainName } from "@/components/ui/chain-chip";
import { SectionHeader } from "@/components/ui/section";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { formatAmount, formatClock, formatCount, formatMinutes, formatUsd, parseTime } from "@/lib/format";
import type { Severity } from "@/lib/types";
import { useDetector, useGoldenHour, useSummary } from "@/lib/use-api";

const SEVERITY: Record<Severity, string> = {
  critical: "#f0544f",
  high: "#ff8550",
  medium: "#f2b33d",
};

function Lead({ minutes }: { minutes: number | null }) {
  if (minutes === null) return <span className="text-ember">Not detected</span>;
  if (minutes < 0) return <span className="text-green">{formatMinutes(-minutes)} before</span>;
  if (minutes < 1) return <span className="text-green">{Math.round(minutes * 60)} s after</span>;
  return <span className="text-ink">{formatMinutes(minutes)} after</span>;
}

export function WatchSection() {
  const det = useDetector();
  const d = det.data;
  const detected = d ? d.replay.filter((r) => r.minutes_after_first_theft !== null) : [];
  const before = d ? d.replay.reduce((s, r) => s + r.usd_before_alert, 0) : 0;
  return (
    <section id="watch" className="shell mt-24 scroll-mt-24" aria-labelledby="watch-title">
      <SectionHeader
        id="watch-title"
        eyebrow="Layer 2 · Watch"
        title="The drain, replayed through the detector"
        lead={
          <p>
            Watch ran over the victim&apos;s wallets as the transfers happened on-chain. For each wallet: when the first stolen transfer left,
            when the first alert would have fired, and how much had gone by then.
          </p>
        }
      />
      {det.error && !d ? (
        <ErrorState error={det.error} onRetry={det.reload} what="The detector replay" />
      ) : !d ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="card overflow-hidden">
            <div className="scroll-x">
              <table className="table min-w-[860px]">
                <caption className="sr-only">Detector replay by wallet</caption>
                <thead>
                  <tr>
                    <th scope="col">Wallet</th>
                    <th scope="col">First theft</th>
                    <th scope="col">First alert</th>
                    <th scope="col">Alert lead</th>
                    <th scope="col" className="r">
                      Gone before alert
                    </th>
                    <th scope="col">Rule</th>
                  </tr>
                </thead>
                <tbody>
                  {d.replay.map((r, i) => (
                    <tr key={`${r.wallet}-${i}`}>
                      <td className="min-w-[190px]">
                        <span className="block whitespace-nowrap text-ink">{r.label || "Wallet"}</span>
                        <span className="text-[12px] text-ink-3">
                          <ChainName chain={r.chain} />
                        </span>
                      </td>
                      <td className="num whitespace-nowrap text-ink-2">{formatClock(r.first_theft_at, true)}</td>
                      <td className="num whitespace-nowrap text-ink-2">{r.first_alert_at ? formatClock(r.first_alert_at, true) : "none"}</td>
                      <td className="num whitespace-nowrap">
                        <Lead minutes={r.minutes_after_first_theft} />
                      </td>
                      <td className="r num text-ink">{formatUsd(r.usd_before_alert)}</td>
                      <td className="mono whitespace-nowrap text-[12px] text-ink-3">{r.rule || "none"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line px-5 py-4 text-[13px] leading-relaxed text-ink-3">
              {formatCount(detected.length)} of {formatCount(d.replay.length)} wallets alerted. A negative lead means the alert fired on the
              delegation itself, before any value moved. {formatUsd(before)} left these wallets before their first alert.
            </p>
          </div>
          <aside className="card p-6">
            <h3 className="text-[15px] font-semibold text-ink">Quiet on ordinary days</h3>
            <p className="mt-3 text-[13.5px] leading-relaxed text-ink-2">
              Over {formatCount(d.baseline.days)} days of normal activity on {formatCount(d.baseline.wallets)} wallets and{" "}
              {formatCount(d.baseline.transactions)} transactions, the same rules raised {formatCount(d.baseline.alerts)} alerts.
            </p>
            <p className="num mt-5 text-[32px] leading-none text-ink">{d.baseline.alerts_per_wallet_day.toFixed(4)}</p>
            <p className="mt-2 text-[12.5px] text-ink-3">alerts per wallet per day</p>
            {d.unread_blocks > 0 ? (
              <p className="mt-4 border-t border-line pt-3 text-[12px] leading-relaxed text-ink-3">
                {formatCount(d.unread_blocks)} {d.unread_blocks === 1 ? "block" : "blocks"} of the busiest wallet could not be read from the free
                explorer and are not counted.
              </p>
            ) : null}
          </aside>
        </div>
      )}
      {d ? (
        <ul className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {d.rules.map((r) => (
            <li key={r.id} className="card-quiet p-5">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[14px] font-semibold text-ink">{r.title}</p>
                <span className="font-mono text-[10.5px] uppercase tracking-[0.1em]" style={{ color: SEVERITY[r.severity] }}>
                  {r.severity}
                </span>
              </div>
              <p className="mt-2 text-[13px] leading-relaxed text-ink-3">{r.description}</p>
              <p className="mono mt-3 text-[11.5px] text-ink-4">{r.id}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

export function RespondSection() {
  const summary = useSummary();
  const gh = useGoldenHour();
  const s = summary.data;
  const t0 = parseTime(gh.data?.t0 ?? s?.stolen.first_at);
  const freezes = s
    ? [...s.now.frozen.by_issuer]
        .map((f) => ({ ...f, minute: t0 !== null && parseTime(f.first_at) !== null ? ((parseTime(f.first_at) as number) - t0) / 60_000 : null }))
        .sort((a, b) => (a.minute ?? 1e9) - (b.minute ?? 1e9))
    : [];
  const maxMinute = Math.max(60, ...freezes.map((f) => f.minute ?? 0));

  return (
    <section id="respond" className="shell mt-24 scroll-mt-24" aria-labelledby="respond-title">
      <SectionHeader
        id="respond-title"
        eyebrow="Layer 3 · Respond"
        title="When each freeze landed"
        lead={
          <p>
            Each issuer froze on its own clock. Respond exists to shorten that clock: it prepares one packet per issuer, bridge and exchange,
            with what each needs to act, while the money is still there.
          </p>
        }
      />
      {summary.error && !s ? (
        <ErrorState error={summary.error} onRetry={summary.reload} what="The freeze data" />
      ) : !s ? (
        <Skeleton className="h-72 w-full" />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="card p-5 sm:p-6">
            <ul className="space-y-4" aria-label="Freezes by issuer, in the order they landed">
              {freezes.map((f, i) => (
                <li key={`${f.issuer}-${f.chain}-${f.symbol}-${i}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-[13.5px]">
                    <span className="text-ink">
                      {f.issuer} <span className="text-ink-3">froze</span> {formatAmount(f.amount, f.symbol)}
                    </span>
                    <span className="num text-[12px] text-ink-3">
                      {f.minute !== null ? `T+${formatMinutes(f.minute)}` : "time unknown"} · {formatClock(f.first_at)} · <ChainName chain={f.chain} />
                    </span>
                  </div>
                  <div className="relative mt-2 h-2 rounded-full bg-bg-3" aria-hidden="true">
                    <div
                      className="absolute inset-y-0 left-0 rounded-full bg-[linear-gradient(90deg,rgba(242,179,61,0.9),rgba(143,211,255,0.9))]"
                      style={{ width: `${Math.max(2, (100 * (f.minute ?? maxMinute)) / maxMinute)}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[12.5px] leading-relaxed text-ink-3">
              Bars measure time from the first stolen transfer to the freeze. Shorter is better: value can only be frozen while it has not been
              swapped or bridged.
            </p>
          </div>
          <aside className="card p-6">
            <h3 className="text-[15px] font-semibold text-ink">What a freeze packet holds</h3>
            <ul className="mt-4 space-y-3 text-[13.5px] leading-relaxed text-ink-2">
              {[
                ["Addresses and balances", "Every address on the trail holding the issuer's asset, with the amount it holds right now."],
                ["The evidence", "The transactions that link each address back to the victim's wallets, hop by hop."],
                ["The case", "Victim, time window, attribution and the reference law enforcement uses."],
                ["Who to reach", "The issuer's documented freeze contact and the format it accepts."],
                ["Refresh", "Packets are rebuilt as money moves, so a request never names yesterday's address."],
              ].map(([k, v]) => (
                <li key={k}>
                  <span className="text-ink">{k}.</span> <span className="text-ink-3">{v}</span>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      )}
    </section>
  );
}
