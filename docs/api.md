# API

Two surfaces with one contract. The public edition serves static JSON at
`https://dharmeshdev1.github.io/goldenhour/api/v1/`, rebuilt about every five minutes. A
self-hosted server serves the same paths and adds the endpoints under `/v1/`.

## Static documents

| Path | What it holds |
| --- | --- |
| `case.json` | The case: victim, attribution, window, publicly reported figures with their sources, timeline, sources. |
| `summary.json` | Stolen (verified on-chain, and reported), held by the attacker, frozen, moved out by category, converted to bitcoin, trail size. |
| `flows.json` | Sankey data: victim, to the chain each value was stolen on, to where it is now. |
| `feed.json` | The 200 latest movements of the stolen money, newest first, with explorer links. |
| `graph.json` | The trail graph: up to 2,000 nodes by value and the edges between them. |
| `golden-hour.json` | Minute-by-minute drained, freezable, frozen and escaped value, events and response scenarios. |
| `detector.json` | The drain detector's replay: first alert per victim wallet, false alarms on held-out days, the learned detector's scores. |
| `guard.json` | The Guard's policy and its decisions on real and reconstructed signing requests. |
| `meta.json` | When the data was generated and what the last tracer run did. |
| `screen/{prefix}.json` | Screening shards, see below. |
| `data/transfers.parquet`, `data/nodes.parquet` | Every transfer touching the trail and every judged address, for your own analysis. |

Every amount is in US dollars at the hour the transfer happened, unless the field says otherwise.
Every timestamp is UTC (ISO 8601 or Unix seconds where named `_ts`).

## Screening without telling us what you screen

1. Spell the address canonically, on its own with no chain: EVM and bitcoin bech32 lowercase,
   base58 encodings unchanged, for example `0x8532FA14ACAf483729C449b8458aedDb900543D4` becomes
   `0x8532fa14acaf483729c449b8458aeddb900543d4`.
2. Hash it: `key = sha256(address)` as lowercase hex.
3. Fetch `screen/{key[:3]}.json`. `entries[key]` is a list with one entry per chain the address
   appears on; keep the entries whose `chain` you are checking.

The host only sees a 3-character prefix shared by about one address in 4,096. Because the chain is
not part of the key, an EVM address costs one request for all six EVM chains, and a string that is
valid on two chains (Tron and Solana, say) costs one request for both. A missing shard has no
entries.

```sh
goldenhour screen 0x8532FA14ACAf483729C449b8458aedDb900543D4 --chain evm
```

An entry's `exposure` is `trail` (most of its money came from the theft) or `direct` (it received
money from the trail, with `received_usd`). No entry means the address is not on this trail. It
does not mean the address is clean.

## Self-hosted endpoints

| Method and path | What it does |
| --- | --- |
| `GET /v1/screen/{chain}/{address}` | Screen one address (`chain` may be `evm`). Answered inside your network. |
| `GET /v1/cases/{case}/summary` | The summary document. |
| `POST /v1/guard/evaluate` | Evaluate a signing request against the server's policy. See [guard.md](guard.md). |
| `GET /v1/stream` | Server-sent events: one `movement` event per new movement, as the tracer finds it. |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | Liveness, readiness, Prometheus metrics. |

## For agents

`goldenhour mcp` serves the Model Context Protocol over stdio with three tools: `screen_address`
(using the private shards), `case_status` and `recent_movements`. Point it at a self-hosted server
with `--api https://your-host/api/v1`.
