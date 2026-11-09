#!/usr/bin/env bash
# Give a cold pipeline a trail to continue from. In order of preference:
#   1. the Actions cache (already restored before this runs; nothing to do),
#   2. the newest daily snapshot from the `state` release, verified with its attestation, which
#      must have been signed by this repository's daily workflow,
#   3. the bootstrap snapshot from the first backfill, verified against the SHA-256 committed in
#      `cases/<case>/bootstrap.sha256`.
# Anything that fails verification is refused. With nothing usable this exits with an error, so the
# run never deploys a trail rebuilt from the seeds alone over the live one. To rebuild from the
# seeds on purpose, run the workflow with cold_start, which sets GOLDENHOUR_ALLOW_COLD_START=1.
set -uo pipefail

state="${GOLDENHOUR_STATE:-state}"
mkdir -p "$state"
if [ -s "$state/goldenhour.db" ]; then
  echo "state restored from the cache"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if gh release download state --pattern 'state.tar.gz' --dir "$tmp" --clobber 2>/dev/null \
  && gh attestation verify "$tmp/state.tar.gz" --repo "$GITHUB_REPOSITORY" \
    --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/daily.yml" --source-ref "refs/heads/main" --deny-self-hosted-runners >/dev/null 2>&1; then
  tar -xzf "$tmp/state.tar.gz" -C "$state" && echo "state restored from the signed daily snapshot" && exit 0
fi

pinned="$(cat cases/*/bootstrap.sha256 2>/dev/null | awk '{print $1}' | head -n1)"
if [ -n "$pinned" ] && gh release download state --pattern 'bootstrap.tar.gz' --dir "$tmp" --clobber 2>/dev/null; then
  actual="$(sha256sum "$tmp/bootstrap.tar.gz" | awk '{print $1}')"
  if [ "$actual" = "$pinned" ]; then
    tar -xzf "$tmp/bootstrap.tar.gz" -C "$state" && echo "state restored from the pinned bootstrap snapshot" && exit 0
  fi
  echo "bootstrap snapshot hash $actual does not match the pinned $pinned; refusing it"
fi

if [ "${GOLDENHOUR_ALLOW_COLD_START:-}" = "1" ]; then
  echo "no verified snapshot; cold start from the seeds, as requested"
  exit 0
fi
echo "::error::No verified trail state (cache, signed daily snapshot or pinned bootstrap). Refusing to rebuild from the seeds unattended; run the workflow with cold_start to do that on purpose."
exit 1
