# Self-hosting

The self-hosted edition is one container: the tracer runs in the background, and the server
serves the web app and the same API as the public site, plus direct screening, the Guard, a live
event stream, alerts and Prometheus metrics. The API and the trail database live on the data
volume; the container's own file system stays read-only. Run it inside your own network when
screening must not leave it, or when alerts must arrive in minutes rather than on the public
site's schedule.

## Docker Compose

```sh
cp deploy/notify.example.yaml deploy/notify.yaml   # edit your alert channels and webhook secret
POSTGRES_PASSWORD=change-me docker compose -f deploy/compose.yaml up -d
curl -s localhost:8080/readyz                      # ready once the first run has published
```

The first run starts from the case's seeds and builds the trail; that takes hours on free data
sources. To start from the public trail instead, restore the signed daily snapshot into the data
volume first:

```sh
gh release download state --repo dharmeshdev1/goldenhour --pattern state.tar.gz
gh attestation verify state.tar.gz --repo dharmeshdev1/goldenhour \
  --signer-workflow dharmeshdev1/goldenhour/.github/workflows/daily.yml --source-ref refs/heads/main
```

## Kubernetes

A Helm chart is in [`deploy/helm/goldenhour`](../deploy/helm/goldenhour). It runs one replica (one
tracer per database), as a non-root user with a read-only root file system and no capabilities.

```sh
kubectl create secret generic goldenhour-db --from-literal=url='postgresql+psycopg://...'
kubectl create secret generic goldenhour-notify --from-file=notify.yaml=deploy/notify.yaml
helm install goldenhour deploy/helm/goldenhour \
  --set database.existingSecret=goldenhour-db --set notify.existingSecret=goldenhour-notify
```

## Configuration

| Variable | Meaning |
| --- | --- |
| `GOLDENHOUR_DATABASE_URL` | SQLAlchemy URL. PostgreSQL (`postgresql+psycopg://...`) or SQLite (default `sqlite:///$GOLDENHOUR_STATE/goldenhour.db`). |
| `GOLDENHOUR_STATE` | Directory for the price cache and, with SQLite, the database. |
| `ETHERSCAN_API_KEY` | Optional. Adds Etherscan's API, which also covers Arbitrum, Base and Polygon, whose free explorers refuse automated clients. |
| `TRONGRID_API_KEY` | Optional. Raises TronGrid's rate limit. |
| `SOLANA_RPC_URL` | Optional. A faster Solana RPC than the public one. |

Server flags: `goldenhour serve --host 0.0.0.0 --port 8080 --interval 300 --notify notify.yaml`.
Outside the image, pass `--web web/out` to serve a local build of the web app (built with
`NEXT_PUBLIC_BASE_PATH=""` so it is served from the root).

## Alerts

Each channel receives movements of the stolen money at or above its `min_usd`, of the kinds it
lists (`move`, `swap`, `bridge`, `exit`, `mix`, `freeze`, `unfreeze`). Channels: generic webhook,
Slack, Microsoft Teams, Discord and PagerDuty. See
[`deploy/notify.example.yaml`](../deploy/notify.example.yaml). The server refuses to start with
the example's placeholder webhook secret or one shorter than 16 characters. Without a
configuration file it runs with alerts off.

Alerts already sent are remembered in `alerts-sent.json` on the data volume, so a restart sends
what moved while the server was down and nothing twice. A brand-new install sends nothing for
movements that happened before it started.

Webhooks are signed. Each request carries `X-Goldenhour-Timestamp` and
`X-Goldenhour-Signature: sha256=<hex>`, an HMAC-SHA256 of `timestamp + "." + body` with your
secret. Verify both, and refuse timestamps older than five minutes:

```python
from goldenhour.server.notify import verify

ok = verify(secret, request.headers["X-Goldenhour-Timestamp"], raw_body, request.headers["X-Goldenhour-Signature"])
```

## Operating it

- `GET /healthz` is liveness, `GET /readyz` is ready once data is published.
- `GET /metrics` exposes `goldenhour_ticks_total`, `goldenhour_alerts_total`,
  `goldenhour_trail_addresses`, `goldenhour_held_usd` and `goldenhour_last_tick_timestamp`.
  Alert on the last tick being older than three intervals.
- A failed tracer run keeps the last good data and is retried on the next interval.
