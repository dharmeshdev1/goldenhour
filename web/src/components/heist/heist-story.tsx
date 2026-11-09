"use client";

import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { ChartLegend, GoldenChart } from "@/components/charts/golden-chart";
import { LayerIcon } from "@/components/ui/layer-icon";
import { SourceRef } from "@/components/ui/source-ref";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { formatClock, formatMinutes, formatTimelineAt, formatUsd, hasTimeOfDay, parseTime } from "@/lib/format";
import { LAYER_BY_ID, layerForStage, type StageLayer } from "@/lib/layers";
import { cleanSeries, seriesAt } from "@/lib/scenarios";
import type { CaseFile } from "@/lib/types";
import { useCase, useGoldenHour, useSummary } from "@/lib/use-api";
import { useMedia } from "@/lib/use-media";

const PIN_QUERY = "(min-width: 1024px) and (prefers-reduced-motion: no-preference)";

interface Stage {
  at: string;
  what: string;
  source: string;
  layer: StageLayer;
}

function stagesFrom(c: CaseFile, firstTheft: number | null, lastTheft: number | null): Stage[] {
  return c.timeline.map((t) => {
    const at = parseTime(t.at);
    // A date without a time is compared by its day, so "2026-10-29" counts as the day of the theft.
    let phase: "before" | "during" | "after" = "before";
    if (at !== null && firstTheft !== null) {
      if (hasTimeOfDay(t.at)) phase = at < firstTheft ? "before" : lastTheft !== null && at > lastTheft ? "after" : "during";
      else phase = at + 86_400_000 <= firstTheft ? "before" : at > firstTheft ? "after" : "during";
    }
    return { ...t, layer: layerForStage(t.what, phase) };
  });
}

function StageCard({ stage, index, total }: { stage: Stage; index: number; total: number }) {
  const layer = LAYER_BY_ID[stage.layer.layer];
  return (
    <article className="flex flex-col gap-6" aria-label={`Stage ${index + 1} of ${total}`}>
      <div>
        <p className="num text-[13px] text-ink-3">
          <span className="text-gold-2">{String(index + 1).padStart(2, "0")}</span> / {String(total).padStart(2, "0")} ·{" "}
          {formatTimelineAt(stage.at)}
        </p>
        <p className="display mt-4 text-[28px] leading-[1.15] text-ink sm:text-[36px]">
          {stage.what} <SourceRef id={stage.source} className="align-middle" />
        </p>
      </div>
      <div
        className={`rounded-2xl border p-5 ${
          stage.layer.breakpoint ? "border-[rgb(242_179_61/0.55)] bg-[rgb(242_179_61/0.08)] shadow-[0_0_60px_-20px_rgba(242,179,61,0.6)]" : "border-line-2 bg-bg-1/80"
        }`}
      >
        <p className="flex items-center gap-2.5 text-[13px] text-ink-3">
          <span className="inline-flex size-8 items-center justify-center rounded-full border border-[rgb(242_179_61/0.4)] text-gold">
            <LayerIcon id={layer.id} size={16} />
          </span>
          <span>
            {stage.layer.breakpoint ? "Where Goldenhour breaks the chain: " : "The layer that answers this: "}
            <Link href={layer.href} className="font-semibold text-gold-2 no-underline hover:text-gold">
              {layer.name}
            </Link>
          </span>
        </p>
        <p className="mt-3 text-[15px] leading-relaxed text-ink-2">{stage.layer.why}</p>
      </div>
    </article>
  );
}

function ChainRail({ stages, active, progressRef }: { stages: Stage[]; active: number; progressRef?: RefObject<HTMLDivElement | null> }) {
  return (
    <ol className="relative space-y-1 pl-6" aria-label="Stages of the attack">
      <span aria-hidden="true" className="absolute top-2 bottom-2 left-[7px] w-px bg-line-2" />
      <div ref={progressRef} aria-hidden="true" className="absolute top-2 bottom-2 left-[7px] w-px origin-top bg-gold" style={{ transform: "scaleY(0)" }} />
      {stages.map((s, i) => {
        const on = i === active;
        const past = i < active;
        return (
          <li key={i} className="relative">
            <span
              aria-hidden="true"
              className={`absolute top-[9px] -left-6 size-[15px] rounded-full border-2 transition-all duration-300 ${
                on ? "border-gold bg-gold shadow-[0_0_18px_rgba(242,179,61,0.7)]" : past ? "border-gold-3 bg-bg" : "border-line-2 bg-bg"
              }`}
            />
            <div className={`rounded-lg px-3 py-1.5 transition-colors duration-300 ${on ? "bg-[rgb(255_236_210/0.05)]" : ""}`}>
              <p className={`num text-[11.5px] ${on ? "text-gold-2" : "text-ink-3"}`}>{formatTimelineAt(s.at)}</p>
              <p className={`line-clamp-1 text-[13px] ${on ? "text-ink" : past ? "text-ink-3" : "text-ink-4"}`}>{s.what}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function PinnedChain({ stages }: { stages: Stage[] }) {
  const section = useRef<HTMLDivElement>(null);
  const progress = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  const n = stages.length;

  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);
    const ctx = gsap.context(() => {
      const end = () => `+=${Math.round(n * window.innerHeight * 0.62)}`;
      // The chain sits above the replay on the page, so it must be measured first: its pin
      // spacing moves everything below it. Higher refreshPriority refreshes earlier.
      ScrollTrigger.create({
        trigger: section.current,
        start: "top top+=64",
        end,
        pin: true,
        anticipatePin: 1,
        refreshPriority: 2,
        onUpdate: (self) => setActive(Math.min(n - 1, Math.floor(self.progress * n * 0.9999))),
      });
      gsap.fromTo(
        progress.current,
        { scaleY: 0 },
        { scaleY: 1, ease: "none", scrollTrigger: { trigger: section.current, start: "top top+=64", end, scrub: 0.3, refreshPriority: 2 } },
      );
    }, section);
    const raf = requestAnimationFrame(() => ScrollTrigger.refresh());
    return () => {
      cancelAnimationFrame(raf);
      ctx.revert();
    };
  }, [n]);

  return (
    <div ref={section} className="flex h-[calc(100vh-64px)] items-center">
      <div className="shell grid w-full grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] items-center gap-16">
        <ChainRail stages={stages} active={active} progressRef={progress} />
        <div className="relative min-h-[420px]">
          <AnimatePresence mode="wait">
            <motion.div
              key={active}
              initial={{ opacity: 0, y: 24, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -16, filter: "blur(4px)" }}
              transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
            >
              <StageCard stage={stages[active]} index={active} total={n} />
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function ListChain({ stages }: { stages: Stage[] }) {
  return (
    <ol className="shell space-y-6">
      {stages.map((s, i) => (
        <li key={i} className="card p-5 sm:p-7">
          <StageCard stage={s} index={i} total={stages.length} />
        </li>
      ))}
    </ol>
  );
}

function DrainReplay({ pinned }: { pinned: boolean }) {
  const gh = useGoldenHour();
  const section = useRef<HTMLDivElement>(null);
  const series = useMemo(() => (gh.data ? cleanSeries(gh.data) : null), [gh.data]);
  const maxMinute = series?.minutes.length ? series.minutes[series.minutes.length - 1] : 0;
  const [minute, setMinute] = useState(0);
  const shownMinute = pinned ? minute : maxMinute;

  useEffect(() => {
    if (!pinned || !maxMinute) return;
    gsap.registerPlugin(ScrollTrigger);
    const ctx = gsap.context(() => {
      ScrollTrigger.create({
        trigger: section.current,
        start: "top top+=64",
        end: () => `+=${Math.round(window.innerHeight * 2.4)}`,
        pin: true,
        anticipatePin: 1,
        refreshPriority: 1,
        onUpdate: (self) => setMinute(Math.round(self.progress * maxMinute)),
      });
    }, section);
    const raf = requestAnimationFrame(() => ScrollTrigger.refresh());
    return () => {
      cancelAnimationFrame(raf);
      ctx.revert();
    };
  }, [pinned, maxMinute]);

  if (gh.error && !gh.data) return <ErrorState error={gh.error} onRetry={gh.reload} what="The minute series" />;
  const t0 = parseTime(gh.data?.t0);
  const drained = series ? seriesAt(series.minutes, series.drained, shownMinute) : 0;
  const freezable = series ? seriesAt(series.minutes, series.freezable, shownMinute) : 0;
  const escaped = series ? seriesAt(series.minutes, series.escaped, shownMinute) : 0;
  const latest = gh.data?.events.filter((e) => e.minute <= shownMinute).at(-1);

  return (
    <div ref={section} className={pinned ? "flex h-[calc(100vh-64px)] items-center" : ""}>
      <div className="shell w-full">
        <div className="card p-5 sm:p-7">
          <div className="flex flex-wrap items-end justify-between gap-6">
            <div>
              <p className="eyebrow">The drain, minute by minute</p>
              <p className="num mt-3 text-[34px] leading-none text-ink sm:text-[44px]">
                <span className="text-gold">T+{shownMinute}</span>
                <span className="ml-3 text-ink-3">{t0 !== null ? formatClock(t0 + shownMinute * 60_000) : ""}</span>
              </p>
            </div>
            <dl className="grid grid-cols-3 gap-6 text-right">
              <div>
                <dt className="eyebrow text-red">Drained</dt>
                <dd className="num mt-1 text-[22px] text-ink">{formatUsd(drained)}</dd>
              </div>
              <div>
                <dt className="eyebrow text-ice">Freezable</dt>
                <dd className="num mt-1 text-[22px] text-ink">{formatUsd(freezable)}</dd>
              </div>
              <div>
                <dt className="eyebrow text-violet">Escaped</dt>
                <dd className="num mt-1 text-[22px] text-ink">{formatUsd(escaped)}</dd>
              </div>
            </dl>
          </div>
          <div className="mt-6">
            {series && gh.data ? (
              <GoldenChart
                series={series}
                events={gh.data.events}
                t0={gh.data.t0}
                reveal={pinned ? shownMinute : undefined}
                height={340}
                label={`Replay of the drain from ${formatClock(gh.data.t0)} to minute ${maxMinute}. ${formatUsd(
                  series.drained[series.drained.length - 1],
                )} drained in total.`}
              />
            ) : (
              <Skeleton className="h-[340px] w-full" />
            )}
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <ChartLegend />
            <p className="min-h-5 text-[13px] text-ink-2" aria-live="off">
              {latest ? (
                <>
                  <span className="num text-ink-3">T+{Math.round(latest.minute)}</span> {latest.label}
                </>
              ) : null}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function DrainSpan({ first, last }: { first: string; last: string }) {
  const a = parseTime(first);
  const b = parseTime(last);
  if (a === null || b === null || b <= a) return null;
  return (
    <span>
      The drain ran from {formatClock(a)} to {formatClock(b)}, {formatMinutes((b - a) / 60_000)}.{" "}
    </span>
  );
}

function ReplayWindow() {
  const gh = useGoldenHour();
  const t0 = parseTime(gh.data?.t0);
  const minutes = gh.data?.minutes ?? [];
  const end = minutes.length ? minutes[minutes.length - 1] : null;
  return (
    <p className="eyebrow mb-3 min-h-4">
      {t0 !== null && end !== null ? `${formatClock(t0).replace(" UTC", "")} to ${formatClock(t0 + end * 60_000)}` : "\u00a0"}
    </p>
  );
}

export function HeistStory() {
  const kase = useCase();
  const summary = useSummary(false);
  const pinned = useMedia(PIN_QUERY);
  const stages = useMemo(() => {
    if (!kase.data) return [];
    const first = parseTime(summary.data?.stolen.first_at) ?? parseTime(kase.data.timeline.find((t) => hasTimeOfDay(t.at))?.at);
    const last = parseTime(summary.data?.stolen.last_at);
    return stagesFrom(kase.data, first, last);
  }, [kase.data, summary.data]);

  return (
    <>
      <div className="shell pt-14 pb-12 sm:pt-20">
        <p className="eyebrow mb-4 text-gold">Anatomy of the heist</p>
        <h1 className="display max-w-4xl text-[46px] text-ink sm:text-[72px]">
          Months to get in. <em className="text-gold-2">Hours</em> to drain.
        </h1>
        <p className="lead mt-6 max-w-2xl text-[18px]">
          How the attackers reached the signing service, step by step from the published sources, and which Goldenhour layer answers each
          step. Goldenhour does not secure laptops; it is built so that a compromised laptop is not enough.
        </p>
        <p className="mt-4 text-[13px] text-ink-3">
          {summary.data ? <DrainSpan first={summary.data.stolen.first_at} last={summary.data.stolen.last_at} /> : null}
          {pinned ? "Scroll to step through the chain." : "Each stage is listed below."} Sources are linked next to each step.
        </p>
      </div>
      <section aria-label="The attack chain">
        {kase.error && !kase.data ? (
          <div className="shell">
            <ErrorState error={kase.error} onRetry={kase.reload} what="The case file" />
          </div>
        ) : !stages.length ? (
          <div className="shell">
            <Skeleton className="h-96 w-full" />
          </div>
        ) : pinned ? (
          <PinnedChain stages={stages} />
        ) : (
          <ListChain stages={stages} />
        )}
      </section>
      <section aria-labelledby="replay-title" className="mt-24">
        <div className="shell mb-8">
          <ReplayWindow />
          <h2 id="replay-title" className="display max-w-3xl text-[34px] text-ink sm:text-[44px]">
            The drain, replayed on a minute axis
          </h2>
          <p className="lead mt-4 max-w-2xl">
            {pinned
              ? "Keep scrolling: each step of the page is a minute of the theft."
              : "From the first transfer on the XRP Ledger to the revocation of the delegations."}
          </p>
        </div>
        <DrainReplay pinned={pinned} />
      </section>
      <section className="shell mt-24">
        <div className="card flex flex-col items-start justify-between gap-6 p-8 md:flex-row md:items-center">
          <div>
            <h2 className="display text-[30px] text-ink">What if the response had started sooner?</h2>
            <p className="mt-2 text-[14.5px] text-ink-3">Move the response clock and see what was still within reach.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Link href="/golden-hour/" className="btn btn-gold">
              Open the golden hour
            </Link>
            <Link href="/guard/" className="btn btn-ghost">
              How Guard refuses the delegations
            </Link>
          </div>
        </div>
      </section>
    </>
  );
}
