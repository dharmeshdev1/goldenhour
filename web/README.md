# Goldenhour web

The website for Goldenhour: the live case file of the Binance theft of 29 October 2026, and the
front end of the five layers (Guard, Watch, Respond, Trace, Screen).

It is a static Next.js export. Every figure on every page is fetched in the browser, at runtime,
from the JSON and Parquet files the pipeline publishes under the API base. The pipeline republishes
them every few minutes without rebuilding the site, and the live pages poll the summary, the feed
and the run metadata every 60 seconds.

## Quick start

Requirements: Node 24 or later and pnpm 10.

```sh
pnpm install
pnpm dev          # http://localhost:3000/goldenhour/ with development fixtures
pnpm typecheck
pnpm lint
pnpm test
pnpm build        # static export in out/
```

`pnpm dev` copies the development fixtures into `public/api/v1` and copies the DuckDB engine into
`public/duckdb` before starting Next. `pnpm build` removes the fixtures first, so they never reach
`out/`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEXT_PUBLIC_BASE_PATH` | `/goldenhour` | Where the site is served. Set it to an empty string to serve from the root. |
| `NEXT_PUBLIC_API_BASE` | `${basePath}/api/v1` | Where the JSON and Parquet files live. Can be another path or an absolute URL. |

Both are read at build time by `next.config.ts`. Nothing else is configurable; there are no keys.

## Pages

| Route | What it shows | Documents |
| --- | --- | --- |
| `/` | War room: the T+ clock, stolen, frozen, still held and moved out, the flow diagram, held and frozen by chain, live movements, the five layers | `summary`, `case`, `flows`, `feed`, `meta` |
| `/heist/` | The attack chain as pinned scrollytelling (GSAP ScrollTrigger), the layer that answers each step, then the drain replayed minute by minute | `case`, `summary`, `golden-hour` |
| `/golden-hour/` | The minute axis of the theft, the response slider with interpolated scenarios and the dusk sky, the event log and assumptions, Watch (detector replay) and Respond (freezes by issuer) | `golden-hour`, `detector`, `summary`, `case` |
| `/trail/` | The trail graph (WebGL through sigma, with a 2D canvas fallback), filters, a pinned detail panel, the largest holders and a DuckDB-WASM SQL console | `graph`, `summary`, `screen/*`, `data/*.parquet` |
| `/screen/` | Private address screening with the k-anonymity explanation and integration snippets | `screen/*`, `graph` (examples only), `summary` |
| `/guard/` | The Guard policy and its cases | `guard` |
| `/method/` | Methodology and limitations, from `src/content/method.ts`, plus the sources in the case file | `case` |
| `/api/` | The static files and the self-hosted REST API | none (links to the files) |

## Structure

```
web/
  fixtures/api/v1/        development data for every endpoint (never deployed)
  scripts/
    make-fixtures.ts      regenerates the fixtures from one seeded simulation (node scripts/make-fixtures.ts)
    fixtures.mjs          use / clean / check: serves fixtures in dev, keeps them out of builds
    copy-duckdb.mjs       copies the DuckDB engine next to the site and fetches its Parquet extension once
  src/
    app/                  one folder per route, the layout, fonts and global styles
    components/
      chrome/             top navigation, footer, freshness indicator, logo
      ui/                 tickers, chips, addresses, citations, code blocks, states
      charts/             Sankey (d3-sankey with particles) and the golden hour chart (d3-shape)
      war-room/ heist/ golden-hour/ trail/ screen/ guard/ method/ api-docs/
    content/method.ts     the methodology text; edit it like Markdown
    lib/
      types.ts            the data contract (see below)
      api.ts              URL building and fetching; refuses unsafe paths
      use-api.ts          one shared store per document, polling, pause while hidden
      normalize.ts        defensive readers for every document
      format.ts           every number, amount, time and address format
      chains.ts           chain names, colors and explorer links, with URL validation
      screen.ts           screening: normalization, node ids, SHA-256 keys, shard lookup
      scenarios.ts        scenario interpolation and minute series
      sankey.ts           flow shaping: validation, merging, cycle breaking, grouping small chains
      layers.ts           the five layers and which one answers each stage of the attack
```

Heavy pieces load only where they are used: the Sankey on the war room, sigma and graphology on
the trail page, and DuckDB-WASM only when someone opens the SQL console.

## Data contract

The types live in `src/lib/types.ts` and mirror what the pipeline writes:

- `case.json`, `summary.json`, `flows.json`, `feed.json`, `graph.json`, `golden-hour.json`,
  `detector.json`, `guard.json`, `meta.json`
- `screen/{prefix}.json`: screening shards keyed by the full SHA-256 of the node id
- `data/transfers.parquet` and `data/nodes.parquet`

`summary.stolen.reported_usd` is optional. When present, the war room shows the verified on-chain
total against it.

Assumptions the site makes where the contract is silent:

- In the Parquet files, `ts` is Unix seconds and `sender`, `receiver` and `node` are node ids
  (`chain:address`); bitcoin transaction nodes are `bitcoin:@tx:<txid>`.
- In `golden-hour.json`, an event `source` that matches a source id in `case.json` is shown as a
  citation; any other value (such as `detector`) is shown without a link.
- A missing screening shard (HTTP 404) means the shard has no entries.
- The self-hosted REST endpoints documented on `/api/` return the same shapes as the static files.

Screening must hash exactly what the pipeline hashes. The node id is `${chain}:${address}`, with
EVM addresses (ethereum, optimism, arbitrum, base, polygon, avalanche) lowercased, bech32 bitcoin
lowercased, and base58 bitcoin, Tron, XRP Ledger and Solana kept as written. The key is the lowercase
hex SHA-256 of the UTF-8 node id, and the shard is its first three characters. An EVM address is
checked on all six EVM chains. `src/lib/screen.test.ts` checks this against the fixture shards.

## Fixtures

`fixtures/api/v1` holds development data for every endpoint, generated by
`scripts/make-fixtures.ts` from one seeded simulation of the theft, so the documents agree with each
other. The magnitudes follow the public facts in the case file; addresses and hashes are random.
The Parquet files are written with DuckDB through `uv` (the JSON is written without it).

How they stay out of production:

1. `pnpm dev` runs `node scripts/fixtures.mjs use`, which copies them into `public/api/v1` and drops
   a marker file, `public/api/.fixtures`. It refuses to touch a `public/api` without the marker,
   because that one came from the real pipeline. It also moves the freshness timestamps to the
   current time, so the dev site reads as live (`GOLDENHOUR_FIXTURES_REBASE=0` turns that off).
2. `pnpm build` runs `node scripts/fixtures.mjs clean` first, which removes `public/api` only when
   the marker is there.
3. After `next build`, `node scripts/fixtures.mjs check` fails the build if the marker reached `out/`.

`public/api/` and `public/duckdb/` are git ignored.

To preview a production build locally, serve `out/` under `/goldenhour/` and put an `api/v1`
directory next to it (for example a copy of `fixtures/api/v1`).

## Rules this code keeps

- No analytics, trackers or third-party requests at runtime. Fonts are self-hosted from fontsource
  packages, and the DuckDB engine and its Parquet extension are served from this site.
- Every string from the data is rendered as text; there is no `dangerouslySetInnerHTML`. Links built
  from data are validated: explorer URLs must be https on a known explorer host, identifiers must
  use a safe character set, and source links must be http or https.
- `prefers-reduced-motion` turns off nonessential motion: the number tickers, particles, the pinned
  scroll scenes (the heist page becomes a list), graph physics (the layout is computed at once) and
  the "Play the clock" animation on the golden hour page.
- Every chart has an accessible description and a table with the same numbers.
