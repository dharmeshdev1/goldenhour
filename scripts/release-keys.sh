#!/usr/bin/env bash
# Choose the web build to deploy and write a cache key for it to $GITHUB_OUTPUT, so the live
# pipeline only downloads it again after a newer one is published.
set -euo pipefail

assets() {
  gh api "repos/${GITHUB_REPOSITORY}/releases/tags/$1" \
    --jq '.assets[] | "\(.name) \(.id)-\(.updated_at)"' 2>/dev/null || true
}

web="$(grep -E '^web\.tar\.gz ' <<<"$(assets site)" | head -n1 || true)"
{
  echo "web_asset=${web%% *}"
  echo "web=${web#* }"
} >> "$GITHUB_OUTPUT"
echo "web: ${web:-none}"
