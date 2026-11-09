"use client";

import { useMemo, type ReactNode } from "react";

/**
 * The golden hour metaphor: a warm horizon at minute zero that fades to night as the response
 * comes later. Layers crossfade on opacity only, which stays cheap while the slider moves.
 */

function stars(count: number) {
  // Deterministic positions, so server and client agree.
  let a = 9301;
  const r = () => ((a = (a * 16807) % 2147483647) / 2147483647);
  return Array.from({ length: count }, () => ({ x: r() * 100, y: r() * 62, s: 0.4 + r() * 1.1, o: 0.35 + r() * 0.65 }));
}

export function Sky({ dusk, children }: { dusk: number; children: ReactNode }) {
  const d = Math.min(1, Math.max(0, dusk));
  const field = useMemo(() => stars(90), []);
  return (
    <div className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div
          className="absolute inset-0"
          style={{
            opacity: 1 - d,
            background:
              "radial-gradient(90% 70% at 50% 108%, rgba(255,196,92,0.55), rgba(232,128,46,0.28) 32%, rgba(120,52,20,0.18) 55%, transparent 78%), linear-gradient(180deg, #0f0b07 0%, #24170b 48%, #4a2a10 100%)",
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            opacity: d,
            background:
              "radial-gradient(80% 60% at 50% 112%, rgba(70,92,170,0.32), rgba(30,40,90,0.18) 45%, transparent 75%), linear-gradient(180deg, #03050b 0%, #070c1c 55%, #0e1630 100%)",
          }}
        />
        <svg className="absolute inset-0 h-full w-full" preserveAspectRatio="none" style={{ opacity: Math.pow(d, 1.4) }}>
          {field.map((s, i) => (
            <circle key={i} cx={`${s.x}%`} cy={`${s.y}%`} r={s.s} fill="#dfe6ff" opacity={s.o} />
          ))}
        </svg>
        <div
          className="absolute left-1/2 h-[340px] w-[340px] rounded-full"
          style={{
            bottom: -170,
            transform: `translate(-50%, ${d * 150}px)`,
            opacity: 1 - d * 0.9,
            background: "radial-gradient(circle, rgba(255,214,128,0.75) 0%, rgba(242,179,61,0.35) 38%, transparent 70%)",
            filter: "blur(2px)",
          }}
        />
        <div className="absolute inset-x-0 bottom-0 h-40 bg-[linear-gradient(180deg,transparent,var(--color-bg))]" />
      </div>
      {children}
    </div>
  );
}
