# ADR 0006: Actions pinned by commit, artifacts attested, the bootstrap pinned by hash

Status: accepted

## Context

The Binance breach began with a tampered package installed from a search ad. A security project
that pulls moving targets into its own pipeline would repeat the mistake it documents.

## Decision

- Every third-party GitHub Action is pinned to a full commit SHA, with the tag in a comment.
  Dependabot proposes updates, so pins do not rot.
- Python dependencies are locked with hashes (`uv.lock`); the web app's with `pnpm-lock.yaml`.
- Jobs that run third-party code get tokens that cannot deploy or sign. Separate jobs that run none
  of that code sign build outputs with GitHub's build provenance attestations and publish them, or
  deploy the site: the live pipeline's `deploy` job holds the Pages and OIDC permissions and runs
  only GitHub's deploy action, on the artifact the `generate` job verified. The container image
  build is the one exception that must push, so it holds `packages: write`, but it cannot mint an
  OIDC token; its provenance is signed in a job of its own.
- The live pipeline refuses a web build or a state snapshot without a valid attestation signed by
  the expected workflow of this repository (`--signer-workflow`), so a file from one workflow cannot
  stand in for another's. The one snapshot built outside CI, the first backfill, is pinned by its
  SHA-256 in `cases/binance-2026-10/bootstrap.sha256`. Without any verified state the pipeline stops
  instead of rebuilding from the seeds, unless a person starts it with `cold_start`.
- Base images and service containers are pinned by digest.
- Before every deploy, `goldenhour verify-site` checks that the site is whole and that the trail has
  not shrunk below 80% of the live one; a sharper drop needs a person to pass `allow_shrink`.

## Consequences

- A compromised tag or a swapped release asset cannot reach the published site.
- Updates are a reviewed change, not a silent one.
