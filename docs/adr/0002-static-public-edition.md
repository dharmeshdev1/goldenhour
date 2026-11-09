# ADR 0002: The public edition is static files rebuilt every five minutes

Status: accepted

## Context

The public site must stay up through traffic spikes (a theft is news), cost nothing to run, and be
reproducible by anyone.

## Decision

GitHub Actions runs the tracer every five minutes, publishes JSON and Parquet files, and deploys
them with the web app to GitHub Pages. The trail database travels between runs in the Actions
cache; a daily job publishes a signed snapshot. A self-hosted edition runs the same code as a
server for teams that need direct screening, alerts and lower latency.

## Consequences

- No server to attack or keep alive; a CDN serves every visitor.
- Freshness is bounded by GitHub's scheduler (minutes, sometimes more). The pipeline restarts
  itself when the scheduler goes quiet, and the site shows the age of its data.
- Anything that needs a request per visitor (direct screening of an arbitrary address with a fresh
  read) is only in the self-hosted edition.
