"use client";

import type { AsyncDuckDB, AsyncDuckDBConnection } from "@duckdb/duckdb-wasm";
import { useCallback, useId, useRef, useState, type KeyboardEvent } from "react";
import { apiUrl, BASE_PATH } from "@/lib/api";
import { formatCount } from "@/lib/format";

/**
 * SQL over the full trail, run in the browser by DuckDB-WASM. The engine comes from this site and
 * the Parquet files from the API; both are fetched only when the first query runs, and no query
 * leaves the browser.
 */

export const EXAMPLES: { label: string; sql: string }[] = [
  {
    label: "The drain, minute by minute",
    sql: `-- Value leaving the victim's wallets, per minute (UTC)
SELECT make_timestamp((t.ts // 60) * 60 * 1000000) AS minute_utc,
       count(*) AS transfers,
       round(sum(t.usd) / 1e6, 1) AS usd_millions
FROM transfers t
JOIN nodes n ON n.node = t.sender
WHERE n.role = 'victim'
GROUP BY minute_utc
ORDER BY minute_utc`,
  },
  {
    label: "Where value left the trail",
    sql: `-- Services that received value from the trail
SELECT n.category, n.label,
       count(*) AS transfers,
       round(sum(t.usd) / 1e6, 2) AS usd_millions
FROM transfers t
JOIN nodes n ON n.node = t.receiver
WHERE n.role = 'service'
GROUP BY n.category, n.label
ORDER BY usd_millions DESC
LIMIT 25`,
  },
  {
    label: "Value by hop from the theft",
    sql: `-- How far from the victim the money sits, hop by hop
SELECT hop,
       count(*) AS addresses,
       round(sum(received_usd) / 1e6, 1) AS received_millions,
       round(sum(held_usd) / 1e6, 1) AS held_millions
FROM nodes
WHERE role IN ('seed', 'trail')
GROUP BY hop
ORDER BY hop`,
  },
];

const SHOW_ROWS = 500;

class MissingDataset extends Error {}

interface Engine {
  db: AsyncDuckDB;
  conn: AsyncDuckDBConnection;
  tables: { name: string; columns: { name: string; type: string }[] }[];
}

let engine: Promise<Engine> | null = null;

async function startEngine(onStatus: (s: string) => void): Promise<Engine> {
  const duckdb = await import("@duckdb/duckdb-wasm");
  const base = `${window.location.origin}${BASE_PATH}`;
  onStatus("Loading the query engine");
  const worker = new Worker(`${base}/duckdb/duckdb-browser-eh.worker.js`);
  const db = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker);
  await db.instantiate(`${base}/duckdb/duckdb-eh.wasm`, null, (p) => {
    if (p.bytesTotal > 0) onStatus(`Loading the query engine, ${Math.round((100 * p.bytesLoaded) / p.bytesTotal)}%`);
  });
  const conn = await db.connect();
  // Extensions come from this site, never from a third party.
  await conn.query("SET autoinstall_known_extensions = false");
  await conn.query("SET autoload_known_extensions = false");
  await conn.query(`SET custom_extension_repository = '${base}/duckdb/extensions'`);
  await conn.query("LOAD parquet");
  for (const name of ["transfers", "nodes"]) {
    onStatus(`Reading ${name}.parquet`);
    const res = await fetch(new URL(apiUrl(`data/${name}.parquet`), window.location.origin), { cache: "no-cache" });
    if (!res.ok) throw new MissingDataset(`data/${name}.parquet is not published yet (HTTP ${res.status}).`);
    await db.registerFileBuffer(`${name}.parquet`, new Uint8Array(await res.arrayBuffer()));
    await conn.query(`CREATE OR REPLACE VIEW ${name} AS SELECT * FROM read_parquet('${name}.parquet')`);
  }
  const tables: Engine["tables"] = [];
  for (const name of ["transfers", "nodes"]) {
    const described = await conn.query(`DESCRIBE ${name}`);
    tables.push({
      name,
      columns: described.toArray().map((r) => {
        const o = r.toJSON() as { column_name: string; column_type: string };
        return { name: o.column_name, type: o.column_type };
      }),
    });
  }
  return { db, conn, tables };
}

interface Result {
  columns: string[];
  numeric: boolean[];
  rows: string[][];
  total: number;
  ms: number;
}

function cell(value: unknown, type = ""): string {
  if (value == null) return "";
  if (/timestamp/i.test(type)) {
    const ms = typeof value === "bigint" ? Number(value / BigInt(1000)) : typeof value === "number" ? value : NaN;
    if (Number.isFinite(ms)) return new Date(ms).toISOString().replace(".000Z", "Z");
  }
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Date) return value.toISOString().replace(".000Z", "Z");
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
  if (typeof value === "object" && Symbol.iterator in (value as object)) return Array.from(value as Iterable<unknown>).map((v) => cell(v)).join(", ");
  return String(value);
}

function toCsv(r: Result): string {
  const q = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  return [r.columns.map(q).join(","), ...r.rows.map((row) => row.map(q).join(","))].join("\n");
}

export default function SqlConsole() {
  const [sql, setSql] = useState(EXAMPLES[0].sql);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [tables, setTables] = useState<Engine["tables"] | null>(null);
  const editorId = useId();
  const last = useRef<Result | null>(null);

  const run = useCallback(async (text: string) => {
    setBusy(true);
    setError(null);
    try {
      if (!engine) {
        engine = startEngine((s) => setStatus(s));
        engine.catch(() => {
          engine = null;
        });
      }
      const e = await engine;
      setTables(e.tables);
      setStatus("Running");
      const t0 = performance.now();
      const table = await e.conn.query(text);
      const ms = performance.now() - t0;
      const fields = table.schema.fields.map((f) => ({ name: f.name, type: String(f.type) }));
      const all = table.toArray();
      const rows = all.slice(0, SHOW_ROWS).map((r) => {
        const o = r.toJSON() as Record<string, unknown>;
        return fields.map((f) => cell(o[f.name], f.type));
      });
      const numeric = fields.map((f) => /^(Int|Uint|Float|Decimal)/i.test(f.type));
      const res = { columns: fields.map((f) => f.name), numeric, rows, total: all.length, ms };
      last.current = res;
      setResult(res);
      setStatus(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus(null);
    } finally {
      setBusy(false);
    }
  }, []);

  const download = () => {
    const r = last.current;
    if (!r) return;
    const blob = new Blob([toCsv(r)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "goldenhour-query.csv";
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      void run(sql);
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_260px]">
      <div className="min-w-0">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Example queries">
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              type="button"
              className={`chip h-8 cursor-pointer px-3 transition-colors ${sql === ex.sql ? "border-gold-3 text-gold-2" : "hover:text-ink"}`}
              onClick={() => {
                setSql(ex.sql);
                void run(ex.sql);
              }}
            >
              {ex.label}
            </button>
          ))}
        </div>
        <label htmlFor={editorId} className="sr-only">
          SQL query
        </label>
        <textarea
          id={editorId}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={onKey}
          spellCheck={false}
          rows={10}
          className="input code mt-3 min-h-[220px] w-full resize-y py-3 leading-relaxed"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" className="btn btn-gold btn-small" onClick={() => void run(sql)} disabled={busy}>
            {busy ? "Running" : "Run query"}
          </button>
          <span className="text-[12px] text-ink-3">Ctrl or Cmd + Enter</span>
          {result ? (
            <button type="button" className="btn btn-ghost btn-small" onClick={download}>
              Download CSV
            </button>
          ) : null}
          <span className="text-[12.5px] text-ink-3" role="status" aria-live="polite">
            {status ?? (result ? `${formatCount(result.total)} rows in ${Math.round(result.ms)} ms${result.total > SHOW_ROWS ? `, first ${SHOW_ROWS} shown` : ""}` : "")}
          </span>
        </div>
        {error ? (
          <p className="mono mt-3 rounded-lg border border-[rgb(240_84_79/0.35)] bg-[rgb(240_84_79/0.08)] p-3 text-[12.5px] whitespace-pre-wrap text-ink-2" role="alert">
            {error}
          </p>
        ) : null}
        {result ? (
          <div className="scroll-x mt-4 max-h-[420px] overflow-y-auto rounded-xl border border-line">
            <table className="table">
              <thead className="sticky top-0 bg-bg-2">
                <tr>
                  {result.columns.map((c, i) => (
                    <th key={c} scope="col" className={result.numeric[i] ? "r" : undefined}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((v, j) => (
                      <td key={j} className={`mono text-[12.5px] whitespace-nowrap ${result.numeric[j] ? "r" : ""}`}>
                        {v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
      <aside className="card-quiet p-4 text-[12.5px]">
        <p className="eyebrow mb-3">Tables</p>
        {(tables ?? [
          { name: "transfers", columns: ["uid", "chain", "tx", "ts", "sender", "receiver", "symbol", "amount", "usd", "kind"].map((n) => ({ name: n, type: "" })) },
          { name: "nodes", columns: ["node", "chain", "address", "role", "category", "label", "hop", "received_usd", "held_usd"].map((n) => ({ name: n, type: "" })) },
        ]).map((t) => (
          <div key={t.name} className="mb-4 last:mb-0">
            <p className="mono text-ink">{t.name}</p>
            <ul className="mt-1 space-y-0.5">
              {t.columns.map((c) => (
                <li key={c.name} className="mono flex justify-between gap-2 text-ink-3">
                  <span>{c.name}</span>
                  <span className="text-ink-4">{c.type.toLowerCase()}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <p className="mt-3 leading-relaxed text-ink-3">
          ts is Unix seconds; sender, receiver and node are node ids (chain:address). Bitcoin transactions appear as bitcoin:@tx:txid.
        </p>
      </aside>
    </div>
  );
}

