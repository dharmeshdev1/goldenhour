import type { ScreenShard } from "@/lib/types";

/**
 * Every document is fetched at runtime from the API base. The pipeline republishes the JSON
 * every few minutes without rebuilding the site, so nothing here is baked in at build time.
 */

/** The site's base path, "" when served from the root. */
export const BASE_PATH = process.env.GOLDENHOUR_BASE_PATH ?? "";
/** Where the JSON API lives. */
export const API_BASE = (process.env.GOLDENHOUR_API_BASE ?? `${BASE_PATH}/api/v1`).replace(/\/+$/, "");

export const REPO_URL = "https://github.com/dharmeshdev1/goldenhour";

// Document paths are fixed names like "summary.json" or "screen/a3f.json". Anything with a
// scheme, a parent segment or an unusual character is refused before it reaches fetch.
const SAFE_PATH = /^[a-z0-9][a-z0-9_-]*(?:\/[a-z0-9][a-z0-9_-]*)*\.(?:json|parquet)$/i;

export function apiUrl(path: string): string {
  const clean = path.replace(/^\/+/, "");
  if (!SAFE_PATH.test(clean)) throw new Error(`refusing to build an API URL for ${JSON.stringify(path)}`);
  return `${API_BASE}/${clean}`;
}

/** A file served next to the site, such as the DuckDB engine. */
export function siteUrl(path: string): string {
  return `${BASE_PATH}/${path.replace(/^\/+/, "")}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(status: number, path: string, message?: string) {
    super(message ?? `Could not load ${path} (HTTP ${status}).`);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }
}

export async function fetchJson<T = unknown>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { cache: "no-cache", signal, credentials: "same-origin" });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(0, path, `Could not reach the API for ${path}.`);
  }
  if (!res.ok) throw new ApiError(res.status, path);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError(res.status, path, `${path} is not valid JSON.`);
  }
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** A screening shard by its three character prefix. A missing shard means no entries: null. */
export async function loadShard(prefix: string): Promise<ScreenShard | null> {
  if (!/^[0-9a-f]{3}$/.test(prefix)) throw new Error(`bad shard prefix ${prefix}`);
  try {
    const shard = await fetchJson<ScreenShard>(`screen/${prefix}.json`);
    if (!shard || typeof shard !== "object" || typeof shard.entries !== "object" || shard.entries === null) {
      throw new ApiError(200, `screen/${prefix}.json`, `screen/${prefix}.json is not a screening shard.`);
    }
    return shard;
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
