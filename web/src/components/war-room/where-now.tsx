"use client";

import dynamic from "next/dynamic";
import { useMemo } from "react";
import { ChainName } from "@/components/ui/chain-chip";
import { SectionHeader } from "@/components/ui/section";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { CHAIN_INFO, isChain } from "@/lib/chains";
import { formatUsd } from "@/lib/format";
import { shapeFlows } from "@/lib/sankey";
import type { Chain, Summary } from "@/lib/types";
import { useFlows, useSummary } from "@/lib/use-api";

const SankeyChart = dynamic(() => import("@/components/charts/sankey-chart"), {
  ssr: false,
  loading: () => <Skeleton className="h-[460px] w-full sm:h-[600px]" label="Loading the flow diagram" />,
});

function ChainBars({ summary }: { summary: Summary }) {
  const rows = useMemo(() => {
    const m = new Map<Chain, { held: number; frozen: number }>();
    for (const r of summary.now.held.by_chain) m.set(r.chain, { held: r.usd, frozen: 0 });
    for (const f of summary.now.frozen.by_issuer) {
      const row = m.get(f.chain) ?? { held: 0, frozen: 0 };
      row.frozen += f.usd;
      m.set(f.chain, row);
    }
    return Array.from(m.entries())
      .filter(([c, v]) => isChain(c) && v.held + v.frozen > 0)
      .sort((a, b) => b[1].held + b[1].frozen - (a[1].held + a[1].frozen));
  }, [summary]);
  const max = Math.max(1, ...rows.map(([, v]) => v.held + v.frozen));
  return (
    <div>
      <div className="mb-4 flex items-center gap-4 text-[12px] text-ink-3">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2 w-3 rounded-sm bg-ember" /> Held
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block h-2 w-3 rounded-sm bg-ice" /> Frozen
        </span>
      </div>
      <ul className="space-y-3.5">
        {rows.map(([chain, v]) => (
          <li key={chain}>
            <div className="flex items-baseline justify-between gap-3 text-[13px]">
              <span className="text-ink-2">
                <ChainName chain={chain} />
              </span>
              <span className="num text-[12px] text-ink-3">
                {v.held > 0 ? <span className="text-ember">{formatUsd(v.held)}</span> : null}
                {v.held > 0 && v.frozen > 0 ? <span> · </span> : null}
                {v.frozen > 0 ? <span className="text-ice">{formatUsd(v.frozen)}</span> : null}
              </span>
            </div>
            <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-bg-3" role="img" aria-label={`${CHAIN_INFO[chain].name}: held ${formatUsd(v.held)}, frozen ${formatUsd(v.frozen)}`}>
              <span className="h-full bg-ember" style={{ width: `${(100 * v.held) / max}%` }} />
              <span className="h-full bg-ice" style={{ width: `${(100 * v.frozen) / max}%` }} />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-5 text-[12px] leading-relaxed text-ink-3">Held is valued at today&apos;s prices; frozen at face value.</p>
    </div>
  );
}

export function WhereNow() {
  const flows = useFlows();
  const summary = useSummary();
  const shaped = useMemo(() => shapeFlows(flows.data), [flows.data]);
  const totals = useMemo(() => {
    const byTone = (tone: string) =>
      shaped.links.filter((l) => shaped.nodes.find((n) => n.id === l.target)?.tone === tone).reduce((s, l) => s + l.usd, 0);
    return { frozen: byTone("frozen"), held: byTone("held") };
  }, [shaped]);
  const description = `Flow diagram. ${formatUsd(shaped.total)} stolen across ${shaped.nodes.filter((n) => n.kind === "chain").length} chain groups. ${formatUsd(
    totals.frozen,
  )} frozen and ${formatUsd(totals.held)} still held, at theft-time prices. The rest reached services. A table with every flow follows.`;

  return (
    <section className="shell mt-28" aria-labelledby="where-now">
      <SectionHeader
        id="where-now"
        eyebrow="Where the money is now"
        title="Every dollar, from the chain it left to where it sits today"
        lead={
          <p>
            Value still held or frozen is priced today, and value that moved out at its price when it moved, so the total is not the value stolen at the time of the theft. Hover a band for its value; the table
            below lists every flow.
          </p>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="card p-4 sm:p-6">
          {flows.error && !flows.data ? (
            <ErrorState error={flows.error} onRetry={flows.reload} what="The flow data" />
          ) : flows.data ? (
            <SankeyChart flow={shaped} summary={description} />
          ) : (
            <Skeleton className="h-[460px] w-full sm:h-[600px]" label="Loading the flow diagram" />
          )}
          {flows.data ? (
            <details className="mt-4 border-t border-line pt-4">
              <summary className="text-[13px] text-ink-3 hover:text-ink">Show every flow as a table</summary>
              <div className="scroll-x mt-3">
                <table className="table">
                  <caption className="sr-only">Flows from the victim to chains and from chains to where the value is now</caption>
                  <thead>
                    <tr>
                      <th scope="col">From</th>
                      <th scope="col">To</th>
                      <th scope="col" className="r">
                        Value
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {shaped.links.map((l) => (
                      <tr key={`${l.source}>${l.target}`}>
                        <td>{shaped.nodes.find((n) => n.id === l.source)?.label ?? l.source}</td>
                        <td>{shaped.nodes.find((n) => n.id === l.target)?.label ?? l.target}</td>
                        <td className="r num">{formatUsd(l.usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ) : null}
        </div>
        <aside className="card p-6" aria-labelledby="by-chain">
          <h3 id="by-chain" className="text-[15px] font-semibold text-ink">
            Held and frozen, by chain
          </h3>
          <p className="mb-5 mt-1 text-[13px] text-ink-3">Where the value sits now, not where it was taken.</p>
          {summary.data ? <ChainBars summary={summary.data} /> : <Skeleton className="h-64 w-full" />}
        </aside>
      </div>
    </section>
  );
}
