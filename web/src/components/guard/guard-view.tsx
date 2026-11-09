"use client";

import { useMemo, useState } from "react";
import { ChainChip } from "@/components/ui/chain-chip";
import { CodeBlock } from "@/components/ui/code-block";
import { ExternalIcon } from "@/components/ui/external";
import { SectionHeader } from "@/components/ui/section";
import { SourceRef } from "@/components/ui/source-ref";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { txUrl } from "@/lib/chains";
import { shortAddress } from "@/lib/format";
import type { GuardCase, GuardDecision } from "@/lib/types";
import { useGuard } from "@/lib/use-api";

const DECISION: Record<GuardDecision, { label: string; color: string; verb: string }> = {
  deny: { label: "Deny", color: "#f0544f", verb: "Denied" },
  hold: { label: "Hold", color: "#f2b33d", verb: "Held" },
  allow: { label: "Allow", color: "#6bd6a0", verb: "Allowed" },
};

export function DecisionBadge({ decision }: { decision: GuardDecision }) {
  const d = DECISION[decision];
  return (
    <span
      className="inline-flex h-7 items-center gap-1.5 rounded-full px-3 font-mono text-[11px] font-semibold uppercase tracking-[0.12em]"
      style={{ color: d.color, background: `color-mix(in oklab, ${d.color} 14%, transparent)`, boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${d.color} 40%, transparent)` }}
    >
      <span aria-hidden="true" className="inline-block size-1.5 rounded-full" style={{ background: d.color }} />
      {d.label}
    </span>
  );
}

function renderValue(v: unknown): string {
  if (v === null) return "null";
  if (typeof v === "string") return /^0x[0-9a-fA-F]{40}$/.test(v) || v.length > 46 ? shortAddress(v, 10, 8) : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function CaseCard({ c }: { c: GuardCase }) {
  const url = c.tx ? txUrl(c.chain, c.tx) : null;
  const d = DECISION[c.decision];
  return (
    <article className="card flex flex-col overflow-hidden" aria-labelledby={`case-${c.id}`}>
      <div className="p-6" style={{ background: `linear-gradient(180deg, color-mix(in oklab, ${d.color} 7%, transparent), transparent)` }}>
        <div className="flex items-start justify-between gap-4">
          <h3 id={`case-${c.id}`} className="text-[17px] leading-snug font-semibold text-ink">
            {c.title}
          </h3>
          <DecisionBadge decision={c.decision} />
        </div>
        <p className="mt-3 text-[13.5px] leading-relaxed text-ink-2">{c.story}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <ChainChip chain={c.chain} />
          {url ? (
            <a href={url} target="_blank" rel="noopener noreferrer" className="chip no-underline hover:text-ink">
              Example transaction <ExternalIcon />
            </a>
          ) : null}
        </div>
      </div>
      <div className="flex-1 border-t border-line px-6 py-5">
        <p className="eyebrow mb-3">Decoded request</p>
        <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-[12.5px]">
          {Object.entries(c.request).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="mono text-ink-3">{k}</dt>
              <dd className="mono min-w-0 break-words text-ink-2" title={typeof v === "string" ? v : undefined}>
                {renderValue(v)}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="border-t border-line px-6 py-5">
        <p className="eyebrow mb-3">{c.reasons.length === 1 ? "Rule applied" : "Rules applied"}</p>
        <ul className="space-y-2.5">
          {c.reasons.map((r, i) => (
            <li key={`${r.rule}-${i}`} className="text-[13px] leading-relaxed">
              <span className="mono mr-2 rounded bg-bg-3 px-1.5 py-0.5 text-[11.5px] text-gold-2">{r.rule}</span>
              <span className="text-ink-2">{r.message}</span>
            </li>
          ))}
        </ul>
      </div>
    </article>
  );
}

export function GuardView() {
  const guard = useGuard();
  const [filter, setFilter] = useState<GuardDecision | "all">("all");
  const cases = useMemo(() => guard.data?.cases ?? [], [guard.data]);
  const counts = useMemo(() => {
    const m = new Map<GuardDecision, number>();
    for (const c of cases) m.set(c.decision, (m.get(c.decision) ?? 0) + 1);
    return m;
  }, [cases]);
  const shown = cases.filter((c) => filter === "all" || c.decision === filter);

  return (
    <>
      <section className="shell" aria-labelledby="why-title">
        <div className="card overflow-hidden">
          <div className="grid lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="border-b border-line p-7 lg:border-r lg:border-b-0">
              <p className="eyebrow mb-3 text-gold">In plain words</p>
              <h2 id="why-title" className="display text-[32px] leading-tight text-ink">
                Why the Binance delegations would have been refused
              </h2>
              <p className="mt-4 text-[14px] leading-relaxed text-ink-3">
                Guard sits between every caller and the signing service, as a second signer that applies a written policy. A request is signed
                only if the policy agrees. It cannot help against keys held outside the signer, and it does not replace endpoint security.
              </p>
            </div>
            <ol className="divide-y divide-line">
              {[
                {
                  title: "Hot wallets never delegate",
                  text: "An EIP-7702 delegation lets another contract act as the wallet. A custody hot wallet has no reason to grant that, so Guard refuses every delegation by rule, whoever asks and however valid their credentials.",
                  source: "tangem",
                },
                {
                  title: "No withdrawal, no signature",
                  text: "Each transfer must match a withdrawal intent from the ledger: destination, asset and amount. The intent comes from the withdrawals service, never from the caller asking for the signature.",
                  source: null,
                },
                {
                  title: "A halt means a halt",
                  text: "On 29 October the delegations kept moving funds after withdrawals were suspended. Under Guard, a suspension blocks every request at the signer, and velocity limits hold anything that moves too much too fast.",
                  source: "thoropass",
                },
              ].map((p, i) => (
                <li key={p.title} className="flex gap-4 p-6">
                  <span className="num mt-0.5 text-[12px] text-gold-3">0{i + 1}</span>
                  <div>
                    <p className="text-[15px] font-semibold text-ink">{p.title}</p>
                    <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">
                      {p.text} {p.source ? <SourceRef id={p.source} /> : null}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      <section className="shell mt-20" aria-labelledby="policy-title">
        <SectionHeader
          id="policy-title"
          eyebrow="The policy"
          title="One file, reviewed like code"
          lead={<p>The policy that produced every decision on this page. Rules are evaluated for each request; deny wins over hold, and hold over allow.</p>}
        />
        {guard.error && !guard.data ? (
          <ErrorState error={guard.error} onRetry={guard.reload} what="The Guard cases" />
        ) : guard.data ? (
          <CodeBlock code={guard.data.policy_yaml} lang="yaml" label="guard-policy.yaml" maxHeight={560} />
        ) : (
          <Skeleton className="h-96 w-full" />
        )}
      </section>

      <section className="shell mt-20" aria-labelledby="cases-title">
        <SectionHeader
          id="cases-title"
          eyebrow="Cases"
          title="The policy, applied"
          lead={<p>Requests modeled on the theft, next to ordinary ones that must keep working. Each shows the decoded request, the decision and the rules that decided it.</p>}
          aside={
            cases.length ? (
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter cases by decision">
                {(["all", "deny", "hold", "allow"] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    aria-pressed={filter === k}
                    onClick={() => setFilter(k)}
                    className={`chip h-8 cursor-pointer px-3 ${filter === k ? "border-gold-3 text-gold-2" : "hover:text-ink"}`}
                  >
                    {k === "all" ? "All" : DECISION[k].verb}
                    <span className="num text-[11px] text-ink-3">{k === "all" ? cases.length : counts.get(k) ?? 0}</span>
                  </button>
                ))}
              </div>
            ) : null
          }
        />
        {guard.data ? (
          <div className="grid gap-5 md:grid-cols-2">
            {shown.map((c) => (
              <CaseCard key={c.id} c={c} />
            ))}
          </div>
        ) : guard.error ? null : (
          <div className="grid gap-5 md:grid-cols-2">
            <Skeleton className="h-80 w-full" />
            <Skeleton className="h-80 w-full" />
          </div>
        )}
      </section>
    </>
  );
}
