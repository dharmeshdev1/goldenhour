import type { GoldenHour } from "@/lib/types";

/**
 * The "if the response had started at minute N" slider. The pipeline computes a handful of
 * scenarios; between them the site interpolates linearly, which is the honest reading of a
 * piecewise model with no more points than it published.
 */

export type ScenarioPoint = GoldenHour["scenarios"][number];

const ok = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Finite points, sorted by minute, one per minute (the last one published wins). */
export function cleanScenarios(raw: unknown): ScenarioPoint[] {
  if (!Array.isArray(raw)) return [];
  const byMinute = new Map<number, ScenarioPoint>();
  for (const p of raw as Partial<ScenarioPoint>[]) {
    if (!p || !ok(p.response_minutes) || !ok(p.freezable_saved_usd) || !ok(p.drain_prevented_usd)) continue;
    byMinute.set(p.response_minutes, {
      response_minutes: p.response_minutes,
      freezable_saved_usd: Math.max(0, p.freezable_saved_usd),
      drain_prevented_usd: Math.max(0, p.drain_prevented_usd),
    });
  }
  return Array.from(byMinute.values()).sort((a, b) => a.response_minutes - b.response_minutes);
}

/**
 * The scenario at minute N: exact at published points, linear in between, and held flat beyond
 * the first and last points (the model says nothing outside them).
 */
export function interpolateScenario(points: ScenarioPoint[], minute: number): ScenarioPoint | null {
  if (!points.length || !ok(minute)) return null;
  const first = points[0];
  const last = points[points.length - 1];
  if (minute <= first.response_minutes) return { ...first, response_minutes: minute };
  if (minute >= last.response_minutes) return { ...last, response_minutes: minute };
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].response_minutes <= minute) lo = mid;
    else hi = mid;
  }
  const a = points[lo];
  const b = points[hi];
  const t = (minute - a.response_minutes) / (b.response_minutes - a.response_minutes);
  return {
    response_minutes: minute,
    freezable_saved_usd: a.freezable_saved_usd + t * (b.freezable_saved_usd - a.freezable_saved_usd),
    drain_prevented_usd: a.drain_prevented_usd + t * (b.drain_prevented_usd - a.drain_prevented_usd),
  };
}

/** The value of a minute series at minute m, interpolated between samples and clamped at the ends. */
export function seriesAt(minutes: number[], values: number[], m: number): number {
  const n = Math.min(minutes.length, values.length);
  if (n === 0 || !ok(m)) return 0;
  if (m <= minutes[0]) return values[0] ?? 0;
  if (m >= minutes[n - 1]) return values[n - 1] ?? 0;
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (minutes[mid] <= m) lo = mid;
    else hi = mid;
  }
  const span = minutes[hi] - minutes[lo];
  if (span <= 0) return values[hi];
  const t = (m - minutes[lo]) / span;
  return values[lo] + t * (values[hi] - values[lo]);
}

/** The minute axis as published, dropping samples whose series values are not all finite. */
export function cleanSeries(g: Pick<GoldenHour, "minutes" | "drained_usd" | "freezable_usd" | "escaped_usd">) {
  const minutes: number[] = [];
  const drained: number[] = [];
  const freezable: number[] = [];
  const escaped: number[] = [];
  const n = Math.min(g.minutes?.length ?? 0, g.drained_usd?.length ?? 0, g.freezable_usd?.length ?? 0, g.escaped_usd?.length ?? 0);
  let previous = -Infinity;
  for (let i = 0; i < n; i++) {
    const m = g.minutes[i];
    const d = g.drained_usd[i];
    const f = g.freezable_usd[i];
    const e = g.escaped_usd[i];
    if (!ok(m) || !ok(d) || !ok(f) || !ok(e) || m <= previous) continue;
    previous = m;
    minutes.push(m);
    drained.push(d);
    freezable.push(f);
    escaped.push(e);
  }
  return { minutes, drained, freezable, escaped };
}
