"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { ApiError, fetchJson } from "@/lib/api";
import {
  normalizeCase,
  normalizeDetector,
  normalizeFeed,
  normalizeFlows,
  normalizeGoldenHour,
  normalizeGraph,
  normalizeGuard,
  normalizeMeta,
  normalizeSummary,
} from "@/lib/normalize";
import type { CaseFile, Detector, Feed, FlowGraph, GoldenHour, GuardCases, Meta, Summary, TrailGraph } from "@/lib/types";

/**
 * One shared store per document: every component reading summary.json shares a single request
 * and a single poll timer. Polls pause while the tab is hidden and catch up when it returns.
 */

export interface ApiState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** When the data was last loaded (ms since epoch), or null. */
  loadedAt: number | null;
}

interface Entry {
  snapshot: ApiState<unknown>;
  normalize: (raw: unknown) => unknown;
  listeners: Set<() => void>;
  inflight: Promise<void> | null;
  pollers: Map<symbol, number>;
  timer: ReturnType<typeof setInterval> | null;
}

const EMPTY: ApiState<unknown> = { data: null, error: null, loading: true, loadedAt: null };
const store = new Map<string, Entry>();

function entryFor(path: string, normalize: (raw: unknown) => unknown): Entry {
  let e = store.get(path);
  if (!e) {
    e = { snapshot: EMPTY, normalize, listeners: new Set(), inflight: null, pollers: new Map(), timer: null };
    store.set(path, e);
  }
  return e;
}

function publish(e: Entry, next: Partial<ApiState<unknown>>) {
  e.snapshot = { ...e.snapshot, ...next };
  for (const l of e.listeners) l();
}

function load(path: string, e: Entry): Promise<void> {
  if (e.inflight) return e.inflight;
  if (e.snapshot.data === null && !e.snapshot.loading) publish(e, { loading: true });
  e.inflight = fetchJson(path)
    .then((raw) => publish(e, { data: e.normalize(raw), error: null, loading: false, loadedAt: Date.now() }))
    .catch((err: unknown) => {
      // Keep the last good data on a failed poll; the error is still reported.
      publish(e, { error: err instanceof Error ? err : new ApiError(0, path), loading: false });
    })
    .finally(() => {
      e.inflight = null;
    });
  return e.inflight;
}

function reschedule(path: string, e: Entry) {
  if (e.timer) clearInterval(e.timer);
  e.timer = null;
  const periods = Array.from(e.pollers.values());
  if (!periods.length) return;
  const every = Math.min(...periods);
  e.timer = setInterval(() => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    void load(path, e);
  }, every);
}

const serverSnapshot = () => EMPTY;

function useDoc<T>(path: string | null, normalize: (raw: unknown) => T, pollMs?: number): ApiState<T> & { reload: () => void } {
  const e = path ? entryFor(path, normalize) : null;

  const subscribe = useCallback(
    (cb: () => void) => {
      if (!e) return () => {};
      e.listeners.add(cb);
      return () => {
        e.listeners.delete(cb);
      };
    },
    [e],
  );
  const snapshot = useSyncExternalStore(subscribe, () => (e ? e.snapshot : EMPTY), serverSnapshot) as ApiState<T>;

  useEffect(() => {
    if (!e || !path) return;
    if (e.snapshot.data === null || e.snapshot.error) void load(path, e);
    if (!pollMs) return;
    const id = Symbol(path);
    e.pollers.set(id, pollMs);
    reschedule(path, e);
    const onVisible = () => {
      const age = Date.now() - (e.snapshot.loadedAt ?? 0);
      if (document.visibilityState === "visible" && age >= pollMs) void load(path, e);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      e.pollers.delete(id);
      reschedule(path, e);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [e, path, pollMs]);

  const reload = useCallback(() => {
    if (e && path) void load(path, e);
  }, [e, path]);

  return { ...snapshot, reload };
}

/** How often the live documents are polled. */
export const POLL_MS = 60_000;

export const useCase = () => useDoc<CaseFile>("case.json", normalizeCase);
export const useSummary = (poll = true) => useDoc<Summary>("summary.json", normalizeSummary, poll ? POLL_MS : undefined);
export const useFeed = (poll = true) => useDoc<Feed>("feed.json", normalizeFeed, poll ? POLL_MS : undefined);
export const useMeta = () => useDoc<Meta>("meta.json", normalizeMeta, POLL_MS);
export const useFlows = () => useDoc<FlowGraph>("flows.json", normalizeFlows);
export const useGraph = () => useDoc<TrailGraph>("graph.json", normalizeGraph);
export const useGoldenHour = () => useDoc<GoldenHour>("golden-hour.json", normalizeGoldenHour);
export const useDetector = () => useDoc<Detector>("detector.json", normalizeDetector);
export const useGuard = () => useDoc<GuardCases>("guard.json", normalizeGuard);
