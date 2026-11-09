"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { formatElapsed, formatUtc, parseTime } from "@/lib/format";
import { useCase, useSummary } from "@/lib/use-api";
import { RelativeTime } from "@/components/ui/time";

function useClock(everyMs: number): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    const raf = requestAnimationFrame(() => setNow(Date.now()));
    const id = setInterval(() => setNow(Date.now()), everyMs);
    return () => {
      cancelAnimationFrame(raf);
      clearInterval(id);
    };
  }, [everyMs]);
  return now;
}

function TPlusClock() {
  const summary = useSummary();
  const now = useClock(1000);
  const first = parseTime(summary.data?.stolen.first_at);
  const elapsed = first !== null && now !== null ? formatElapsed(now - first) : null;
  return (
    <div className="card w-full max-w-md p-6 sm:p-7">
      <p className="eyebrow flex items-center gap-2">
        <span aria-hidden="true" className="inline-block size-1.5 rounded-full bg-red text-red animate-pulse-dot" />
        Time since the first stolen transfer
      </p>
      <p className="num mt-4 text-[31px] leading-none whitespace-nowrap text-ink sm:text-[48px]" aria-live="off">
        <span className="text-gold">T+</span>
        <span className="ml-2">{elapsed ?? <span className="text-ink-4">--:--:--</span>}</span>
      </p>
      <dl className="mt-5 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        <dt className="text-ink-3">First transfer</dt>
        <dd className="num text-ink-2">{summary.data ? formatUtc(summary.data.stolen.first_at, true) : "loading"}</dd>
        <dt className="text-ink-3">Last transfer</dt>
        <dd className="num text-ink-2">{summary.data ? formatUtc(summary.data.stolen.last_at, true) : "loading"}</dd>
        <dt className="text-ink-3">Last movement</dt>
        <dd className="num text-ink-2">
          {summary.data?.last_movement_at ? (
            <>
              <RelativeTime value={summary.data.last_movement_at} /> on the trail
            </>
          ) : summary.data ? (
            "none recorded"
          ) : (
            "loading"
          )}
        </dd>
      </dl>
    </div>
  );
}

function Backdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(70% 55% at 72% 108%, rgba(242,179,61,0.30), rgba(242,120,40,0.10) 45%, transparent 72%), radial-gradient(40% 30% at 15% 0%, rgba(240,84,79,0.07), transparent 70%)",
        }}
      />
      <svg className="absolute bottom-0 left-1/2 h-[820px] w-[1600px] -translate-x-[28%] translate-y-[52%] opacity-70" viewBox="0 0 1600 1600">
        {[180, 300, 420, 560, 720].map((r, i) => (
          <circle key={r} cx="800" cy="800" r={r} fill="none" stroke="#f2b33d" strokeOpacity={0.09 - i * 0.012} strokeWidth="1" />
        ))}
      </svg>
      <div className="absolute inset-x-0 bottom-0 h-px bg-[linear-gradient(90deg,transparent,rgba(242,179,61,0.55)_60%,transparent)]" />
    </div>
  );
}

export function Hero() {
  const kase = useCase();
  const c = kase.data;
  return (
    <section className="relative overflow-hidden border-b border-line" aria-labelledby="hero-title">
      <Backdrop />
      <div className="shell relative pt-14 pb-16 sm:pt-20 sm:pb-24">
        <p className="eyebrow flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="inline-flex items-center gap-2 text-red">
            <span aria-hidden="true" className="inline-block size-1.5 rounded-full bg-red" />
            Live case file
          </span>
          <span className="text-ink-3">{c ? c.title : "Loading case"}</span>
          {c?.attribution ? <span className="hidden text-ink-3 md:inline">Attributed to {c.attribution}</span> : null}
        </p>
        <h1 id="hero-title" className="display mt-6 max-w-5xl text-[50px] text-ink sm:text-[76px] lg:text-[96px]">
          The <em className="text-gold">first hour</em> decides how much comes back.
        </h1>
        <div className="mt-10 grid items-end gap-10 lg:grid-cols-[1fr_auto]">
          <div className="max-w-xl">
            <p className="lead text-[18px]">
              Goldenhour is open-source software for the minutes after a crypto theft: it guards the signer, catches the drain, files the
              freezes and follows the money across chains. This is its live case file on the theft from Binance.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/golden-hour/" className="btn btn-gold">
                Replay the golden hour
              </Link>
              <Link href="/heist/" className="btn btn-ghost">
                How the heist worked
              </Link>
            </div>
          </div>
          <TPlusClock />
        </div>
      </div>
    </section>
  );
}
