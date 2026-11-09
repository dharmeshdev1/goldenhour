# Operations

How the public deployment runs, what to look at when something looks wrong, and how to fix it.
Everything runs on GitHub: Actions for compute, the Actions cache and releases for state, Pages for
hosting.

## What runs when

| Workflow | Schedule | Does |
| --- | --- | --- |
| Live pipeline (`pipeline.yml`) | Every 5 minutes | Re-read moved trail addresses, judge new ones, publish the API, deploy Pages. |
| Daily (`daily.yml`) | 04:17 UTC | A one-hour pass over every holder, then a signed snapshot of the trail to the `state` release. |
| Web (`web.yml`) | On changes under `web/` | Build the web app, sign it, publish it to the `site` release, trigger a deploy. |
| CI (`ci.yml`) | Every push and pull request | Lint and tests on Python 3.12 and 3.13, storage tests on PostgreSQL, web checks and build, container build. |
| CodeQL (`codeql.yml`) | Pushes, pull requests, weekly | Static analysis of Python, TypeScript and the workflows themselves. |
| Release (`release.yml`) | `v*` tags | Wheel, sdist and a multi-arch image on GHCR, each with build provenance. |

GitHub runs scheduled workflows on a best-effort basis. The live pipeline's last step checks
whether the scheduler has started a run in the past fifteen minutes; if not, it waits until about
five minutes have passed and starts the next run itself.

## Health at a glance

- `api/v1/meta.json`: when the data was generated and what the last tracer run did (addresses
  fetched, joined, errors).
- `api/v1/summary.json`: `last_movement_at` is the newest movement seen on the trail. If it is old
  while public reports say the money is moving, a source is failing.

## Runbook

**The site is stale.** Look at the Live pipeline runs. If none is running or queued, start one:
`gh workflow run pipeline.yml`. It keeps itself going from there.

**A source is failing.** The tracer logs `failed on <node>` with the error and carries on. A 403 or
429 from one host slows that host down and the next run resumes where this one stopped. Persistent
403s from an explorer mean it now refuses automated clients: set `ETHERSCAN_API_KEY` (a free key
covers Ethereum and most EVM chains) as a repository secret.

**The trail state was evicted from the cache.** The next run restores the latest signed daily
snapshot, verified with `gh attestation verify` against the daily workflow, or else the pinned
bootstrap snapshot, and continues. If neither is available the run stops with an error rather than
rebuild from the seeds alone. To rebuild on purpose, run the Live pipeline by hand with
`cold_start`. Deploys stay refused until the rebuilt trail is back to 80% of the live one, so the
public site keeps its last good version meanwhile.

**A deploy was refused.** `goldenhour verify-site` printed why: the web app was missing, an API
file was missing or unreadable, or the trail or the stolen total fell below 80% of the live site.
A rule change can shrink the trail on purpose; then run the Live pipeline by hand with
`allow_shrink`.

**Scheduled runs stopped after a quiet stretch.** GitHub disables scheduled workflows in a public
repository after 60 days without activity in it. Re-enable the Live pipeline and Daily workflows
from the Actions tab, or push any commit before then.

**A new address must be added by hand** (a first hop on a chain the tracer does not reach from the
seeds). Add it to `extra_seeds` in `cases/<case>/case.yaml` with its reason and the transactions
that prove it. Case seeds override earlier verdicts.

**An address is on the trail and should not be.** Find its row in `api/v1/data/nodes.parquet`: its
`reason` says why it joined. If the evidence is wrong, fix the cause (usually a missing service
label in `labels/` or a missing victim wallet in the case), not the verdict.
