# Security

Goldenhour publishes claims about addresses and runs unattended on public data. This is how it
protects its users, the people its data describes, and itself.

## Reporting a problem

Please report vulnerabilities privately through GitHub's security advisories for this repository,
not in a public issue. If an address of yours is on the trail and you believe it should not be,
open an issue with the address and the transactions that explain it. Every verdict on the site
comes with its reason, so a correction is a matter of evidence.

## What the data can and cannot be used for

"On the trail" means most of the value an address ever received came from the theft. It is a
statement about money, not about people. Exchanges and payment companies should treat a hit as a
reason to look closer, not as a verdict. A miss means "not on this trail", never "clean".

**Framing.** Anyone can send money to anyone. An attacker could send stolen funds to an innocent
address to put it on the trail. The majority rule limits this to addresses whose own history is
small compared with what they were sent, the dollar floor ignores addresses paid less than $1,000
in total, and every entry shows when
and from where the money arrived, so a framed address is visible as one: it received and did
nothing else.

## Threat model for the platform itself

| Threat | Defense |
| --- | --- |
| Malicious strings in chain data (token names, memos, labels) reaching a browser | The web app renders every external string as text, never as HTML. Strings are length-capped when stored. |
| Path traversal through attacker-chosen identifiers | Every address is validated against its chain's format before it reaches storage, a file name or a URL (`models.normalize_address`). Screening shard names are checked to be three hex characters. The static file server resolves paths and refuses anything outside the site directory. |
| Spam inflating the numbers | Only listed assets and the victim's own tokens have a dollar value ([ADR 0004](adr/0004-valuation-allowlist.md)). |
| A compromised dependency or action in the pipeline | Actions and base images pinned, locked dependencies, jobs that run third-party code hold no permission to deploy or sign, and the deploy job runs nothing but GitHub's deploy action ([ADR 0006](adr/0006-supply-chain.md)). |
| A swapped snapshot or web build | Refused unless it carries a build attestation signed by the expected workflow of this repository, from `main`, on a GitHub-hosted runner; the one hand-built snapshot is pinned by SHA-256 in the repository. |
| A broken run replacing a good site | `goldenhour verify-site` refuses a deploy without the web app, with a missing or unreadable API file, or with a trail sharply smaller than the live one; the live site keeps its last good version. |
| Learning what a compliance team screens | The public screening index answers from static shards: the browser hashes the address and fetches one 1-in-4,096 shard, the same for every chain ([ADR 0003](adr/0003-k-anonymous-screening.md)). |
| Forged or replayed alerts | Webhooks are signed with HMAC-SHA256 over a timestamp and the exact body; receivers reject stale timestamps. The server refuses a placeholder or short signing secret. |
| Abuse of the self-hosted server | No endpoint fetches a URL a client supplies. The container runs as a non-root user with a read-only file system and no capabilities. |
| Getting free data sources banned | Per-host token buckets, `x-ratelimit-*` headers obeyed, backoff on 429, an honest User-Agent that links here. |

## The Guard

The Guard is a security control for other people's signing systems. Its own threat model,
including what it cannot protect against, is in [guard.md](guard.md).
