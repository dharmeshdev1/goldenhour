#!/usr/bin/env bash
# Download the chosen web build into ./web-build unless the cache restored it, then lay it out under
# ./site for the API to be published on top. Only builds signed by this repository's web workflow
# are used: the attestation ties the file to that workflow here, so a file swapped in by anything
# else is refused. Without a verified web build this fails, and the run does not deploy: a site
# with the API but no app would replace the live one.
set -uo pipefail

mkdir -p web-build site
if [ -n "${WEB_ASSET:-}" ] && [ -z "$(ls -A web-build 2>/dev/null)" ]; then
  tmp="$(mktemp -d)"
  if gh release download site --pattern "$WEB_ASSET" --dir "$tmp" --clobber \
    && gh attestation verify "$tmp/$WEB_ASSET" --repo "$GITHUB_REPOSITORY" \
      --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/web.yml" --source-ref "refs/heads/main" --deny-self-hosted-runners >/dev/null \
    && tar -xzf "$tmp/$WEB_ASSET" -C web-build; then
    echo "downloaded and verified $WEB_ASSET"
  else
    echo "could not fetch or verify $WEB_ASSET"
    rm -rf web-build/*
  fi
  rm -rf "$tmp"
fi
if [ ! -s web-build/index.html ]; then
  echo "::error::No verified web build; not deploying the API without the app."
  exit 1
fi
echo "web: $(cat web-build/BUILD 2>/dev/null || echo unknown)"
cp -R web-build/. site/
