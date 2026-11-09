# Guard

Guard is a policy co-signer for custody wallets. It sits in front of a signing service, or holds
one share of the signing key, and answers one question for every request: should this be signed?
It reads exactly what the key would sign, turns it into explicit actions, checks those actions
against a written policy, and answers **allow**, **hold** or **deny** with its reasons. A hold waits
for a quorum of named approvers. Every decision is written to a signed, hash-chained audit log.

Guard exists because two of the largest thefts in the industry were signing failures, not key
thefts:

* On 21 February 2025, Bybit's Ethereum cold wallet, a Safe multisig, executed an
  `execTransaction` its owners had signed, whose `operation` field was 1: a delegatecall into an
  attacker's contract, which rewrote the Safe. Malicious code injected into the Safe{Wallet} web
  interface had changed what the signers were asked to sign. About $1.5 billion was drained.
* On 29 October 2026, a container deployed with stolen credentials reached Binance's wallet
  signing service, which signed what the attacker asked, including 50 EIP-7702 delegations. The
  delegations kept working after withdrawals were suspended, until Binance revoked them at
  00:10 UTC on 30 October.

In both cases the key did what it was told. Guard is the part that decides what the key is told.

The code is in `src/goldenhour/guard/`, the policies in `policies/`.

## How a decision is made

1. **Read the request.** A request names the custody wallet, the chain and exactly what is to be
   signed: a transaction (raw bytes, decoded fields, or both, which must agree), EIP-712 typed
   data, or a message. Anything ambiguous is refused: a signed raw transaction must be signed by
   the wallet the request names, typed data may not carry fields its types do not declare, and a
   transaction without a destination must say `"to": null` explicitly. A request Guard cannot read,
   for whatever reason, is denied and logged; it never crashes the guard.
2. **Decode it into actions.** Native transfers, ERC-20 `transfer`, `transferFrom`, `approve` and
   `increaseAllowance`, `setApprovalForAll`, EIP-2612 and DAI permits (signed, or submitted
   on-chain), EIP-3009 transfer authorizations (USDC's `transferWithAuthorization` and
   `receiveWithAuthorization`, signed or submitted), Permit2's `approve`, allowances and transfers,
   Safe `execTransaction` (all ten parameters, with the inner call decoded in turn), Safe `SafeTx`
   typed data, Safe owner, threshold, module, guard and fallback handler changes, Safe
   `approveHash`, proxy upgrades (directly or through a ProxyAdmin), handovers of a contract's admin
   or ownership, `multicall` and MultiSend batches, EIP-7702 delegations from type 4 transactions
   and from bare authorization messages, and messages that are nothing but a 32-byte hash. Anything
   else sent to a contract becomes an `unknown_call` that carries its selector. The decoder knows nothing about the policy, so what Guard says a request
   does never depends on what it is configured to allow.
3. **Run the rules.** Each rule reads the actions and returns findings: `deny`, `hold`, or `note`
   for an explanation that blocks nothing.
4. **Combine.** Any deny wins. Otherwise any hold wins, unless a quorum of approvers has signed this
   exact request. Otherwise the request is allowed. A quorum can release a hold, never a deny.
5. **Commit and log.** When a request is allowed, the withdrawal intent it matched is consumed and
   its USD value is recorded for the velocity limit, in one atomic step that also re-checks the
   velocity limit under the ledger's write lock. If another request consumed the intent, or spent
   the wallet's remaining allowance, first, the request is decided again on the new state. Every
   decision, allowed or not, is appended to the audit log.

Steps 2 to 4 are a pure function, `evaluate(request, policy, ledger, now)`, with the caller's USD
prices inside the request. It reads the ledger and writes nothing, so the same inputs always
produce the same decision and any decision in the audit log can be replayed.

Guard computes two hashes for every request:

* The **signing hash** is what the wallet's key signs: keccak256 of the unsigned transaction
  payload (the EIP-155 form for legacy transactions), of the EIP-712 encoding, or of the message
  with the EIP-191 prefix. EIP-7702 authorization messages are hashed without a prefix, exactly as
  the chain checks them. A signer behind Guard must sign the signing hash Guard returns, never a
  hash supplied by the caller.
* The **request digest** is what approvers sign:
  `keccak256(rlp([b"goldenhour/guard/request/v1", kind, chain_id, wallet, intent_id, preimage]))`,
  where `preimage` is the signing preimage above. It binds what will be signed to the context the
  policy judged it in. Approvals and prices are not part of it.

## Rules

Every rule has a stable id, appears in every policy file, and can be switched off only with an
explicit `enabled: false`. Severities are configurable; the defaults are shown.

| Rule | Default | What it checks | Why |
| --- | --- | --- | --- |
| `known-wallet` | deny (built in) | The request's wallet is listed in the policy. | Guard only signs for wallets it was set up to protect. |
| `well-formed` | deny (built in) | Every part of the request decodes. | A request Guard cannot read in full cannot be vouched for. |
| `no-eip7702-delegation` | deny | No EIP-7702 delegation, in a type 4 transaction or as a signed authorization. Revoking one (delegating to the zero address) is allowed. | A delegation hands the wallet's balance and permissions to another contract's code, on-chain, until it is revoked. Pausing withdrawals does not stop it. |
| `no-delegatecall` | deny | No delegatecall, except to listed targets such as Safe's MultiSendCallOnly. | A delegatecall runs foreign code inside the wallet with its storage and funds. |
| `no-blind-signing` | deny | No message that is nothing but a 32-byte hash (as bytes or as hex text), and no Safe `approveHash`. | A Safe accepts an owner's signature of its transaction hash as a message (with 4 added to `v`), so a blind hash can approve any Safe transaction unread. |
| `no-upgrade-or-owner-change` | deny, or hold inside a change window | Proxy upgrades (direct or through a ProxyAdmin), handovers of a contract's admin or ownership, and changes to a Safe's owners, threshold, modules, guard or fallback handler happen only inside a scheduled change window, with a quorum. | These change who controls the wallet. A module can move funds without owner signatures. |
| `no-unlimited-approval` | deny | No allowance at or above 2^96 - 1, no `setApprovalForAll` grant, and no DAI-style permit, which can only grant everything. | An unlimited approval outlives the transaction that needed it. |
| `approval-allowlist` | deny | Approvals and permits name only listed spenders. Revocations are always allowed, but `approve(spender, 0)` is a revocation only on a token listed as an asset or for the zero address: on an ERC-721 contract it approves the NFT with id 0. | A spender can move funds whenever it likes. |
| `intent-required` | deny | Every outbound transfer, including a signed EIP-3009 authorization, matches an unused, unexpired withdrawal intent: same wallet, chain, asset and destination, for at most the intent's amount. An intent pays one transfer. Safe gas refunds are refused. | A compromised caller can ask for any transfer. It cannot create intents in the withdrawal system's ledger. |
| `velocity` | hold | What a wallet sent in a rolling window, plus this request, stays under a USD limit. The ledger checks it again when it records an allowed request. | Bounds the damage if intents themselves are forged, even when requests arrive at the same moment. |
| `max-per-tx` | hold | One request from one wallet stays under a USD limit. | Large moves get a second pair of eyes. |
| `destination-allowlist` | hold | Wallets with a listed role (treasury by default) pay only listed destinations or the policy's own wallets. | A treasury paying someone new is exactly when a human should look. |
| `explainable` | hold | No `unknown_call` to an unlisted contract, no contract deployment, no unrecognized typed data, no free-form message. | "Guard cannot explain this transaction" is a reason to stop, not to sign. |
| `quorum` | releases holds | At least M valid Ed25519 approvals of the request digest, from distinct approvers listed in the policy. | Holds need a way out that is as accountable as the rules. |

USD values use the caller's prices, keyed by asset symbol, but never below the `price_floor_usd`
the policy sets for the asset. A transfer of an asset the policy does not list, or with no price,
cannot be valued, and the USD rules hold it.

`policies/default.yaml` explains each setting in comments. It is safe to deploy as it stands
because it fails closed: with no wallets listed every request is denied, and with no approvers
listed no hold is ever released. The loader refuses unknown keys, missing rules and any key that
appears twice in one mapping, which YAML would otherwise resolve by quietly keeping the last. `policies/demo.yaml` is the policy behind the public cases; its
approver keys are derived from published seeds, so it must never be deployed.

## Requests

```json
{
  "kind": "evm_tx",
  "chain_id": 1,
  "wallet": "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3",
  "type": 2,
  "nonce": 7311,
  "to": "0x732206669723FA774AEC33aC3aC27C64201D360F",
  "value": "2500000000000000000",
  "data": "0x",
  "gas": 21000,
  "max_fee_per_gas": "30000000000",
  "max_priority_fee_per_gas": "1000000000",
  "intent_id": "wd-20261101-0042",
  "approvals": [],
  "context": {"prices_usd": {"ETH": "3400"}}
}
```

* `kind` is `evm_tx`, `evm_typed_data` (with `typed_data`, the EIP-712 JSON) or `evm_message`
  (with `message`, as hex).
* A transaction may be given as `raw` hex instead of, or as well as, its fields. Legacy (with
  EIP-155 replay protection), type 1, type 2 and type 4 transactions are read, signed or unsigned.
  Blob transactions and legacy signatures without replay protection are refused.
* Type 4 transactions carry `authorization_list` entries of `chain_id`, `address`, `nonce`,
  `y_parity`, `r` and `s`. Guard recovers who signed each one.
* Amounts are base units, and may be JSON numbers, decimal strings or `0x` hex strings.

## Approvals

An approver reviews a held request with `goldenhour guard check`, then signs its digest:

```
goldenhour guard approve request.json --key alice.pem --approver alice --write
```

The signature is Ed25519 over `b"goldenhour/guard/approval/v1" + digest`. Guard counts an approval
only if the approver is listed in the policy, the signature verifies against this request's
digest, and that approver has not already been counted. Anything else is reported and ignored.

## Intents and the ledger

An intent is the withdrawal system's statement that one payment should happen:

```json
{
  "id": "wd-20261101-0042",
  "wallet": "0x66A2f4A63310aCa888F8e578BA5a7093BA3Bb8b3",
  "chain_id": 1,
  "asset": "native",
  "destination": "0x732206669723FA774AEC33aC3aC27C64201D360F",
  "amount": "2500000000000000000",
  "expires_at": "2026-11-01T13:00:00Z"
}
```

`asset` is `native` or a token address. The ledger is a SQLite file (`SqliteLedger`); tests use
`MemoryLedger`. Consuming an intent and recording spending happen in one `BEGIN IMMEDIATE`
transaction, so two guards sharing a ledger cannot both spend one intent.

## Audit log

The log is JSON Lines. Each record holds `seq`, `at`, `event`, `prev_hash`, `hash` (SHA-256 of the
canonical JSON of the first four) and `signature` (the guard's Ed25519 signature of `hash`).
`goldenhour guard verify-log` reports the first record that was altered, removed, reordered or
signed by another key.

Removing records from the end leaves a shorter chain that is still valid, so publish the head
hash somewhere else from time to time and pass the last one you published with `--head`. The log
keeps growing after a head is published, so `verify-log` looks for that hash anywhere in the chain
and reports how many records follow it; if no record has it, records were removed or rewritten.

Appends take an exclusive lock on the log file (a POSIX `flock`) and re-read the last record under
it, so several guard processes sharing one log extend a single chain instead of forking it. A guard
refuses to append to a log whose last record is damaged or not signed by its own key, so a rotated
key starts a new log.

## Threat model

### What Guard stops

* **A compromised caller of the signing service.** Stolen credentials let an attacker send
  requests, not create withdrawal intents. Transfers without an intent are denied, delegations
  and delegatecalls are denied, and anything Guard cannot explain is held for people who can.
* **An injected wallet interface.** What a web page displays does not matter. Guard decodes the
  bytes or typed data the key would actually sign, so a delegatecall dressed as a transfer is
  read as a delegatecall.
* **A malicious deployment.** A container deployed with stolen credentials, or a tampered build of
  the service that calls the signer, is just another caller. Guard runs elsewhere, with its own
  policy, and judges its requests the same way.

### What Guard does not stop

* **Compromise of the Guard host itself.** An attacker who controls Guard controls its answers.
* **Compromise of M approvers.** The quorum is only as strong as its members' keys.
* **A compromised withdrawal system.** Forged intents pass `intent-required`. The velocity and
  per-request limits, and the treasury destination allowlist, bound what they can take.
* **Bad prices.** USD limits use the caller's prices. The policy's price floors stop an asset from
  being priced near zero, but not every mispricing.
* **Other chains.** Guard reads EVM requests only and refuses anything else, so a signer that also
  serves Bitcoin or the XRP Ledger needs a policy layer for those chains too.
* **State Guard cannot see.** Approvals and delegations that already exist on-chain, and the
  behavior of allowlisted contracts, are outside what Guard reads. Listing a contract trusts it.
* **A signer reachable without Guard.** If anything can reach the key without Guard's consent,
  Guard is advice. The deployment has to make it mandatory.

### Protecting Guard

* **Separate infrastructure.** Run Guard in a different account with different credentials, a
  different deployment pipeline and different administrators from the signing service, so one
  stolen token cannot reach both.
* **Hold a share of the key.** Make Guard a required participant in signing: one share of an MPC
  key that the signing protocol cannot complete without, or an owner of a multisig whose threshold
  cannot be met without it. Then a signature needs Guard's consent, rather than its cooperation.
* **Reproducible builds with provenance.** Build Guard from pinned dependencies (`uv.lock`), with a
  reproducible process and a signed provenance attestation, and deploy only images whose digest
  matches. Record the policy digest, which every decision carries, the same way.
* **Ship the log off the host.** Copy the audit log to write-once storage and publish its head
  hash, so even a compromised Guard cannot rewrite its history unnoticed.

## Deployment patterns

* **Gate.** Guard is the only network path to the signing service. The signer accepts a request
  only with Guard's allow, and signs the signing hash Guard computed.
* **Co-signer.** Guard holds one share of an MPC key, or one owner key of a multisig, and
  contributes its share only after an allow. This is the stronger pattern: bypassing Guard means
  compromising Guard.
* **Safe owner.** For a Safe, Guard checks `SafeTx` typed data before signing it as one of the
  owners, the same kind of request Bybit's signers were asked to sign. With the threshold equal to the number of
  owners, no transaction executes without Guard.

In every pattern, `goldenhour guard check` exits 0 for allow, 3 for hold and 4 for deny, so a
pipeline can gate on the exit status alone.

## Worked example: Bybit, 21 February 2025

Transaction `0x46deef0f52e3a983b67abf4714448a41dd7ffd6d32d32da69d62081c68ad7882`, as stored in
`tests/guard/fixtures/` after fetching it from a public Ethereum node. Its fields hash to the
transaction hash, and its signature recovers to the account that submitted it,
`0x0fa09C3A328792253f8dee7116848723b72a6d2e`. Read from the chain:

* It calls `execTransaction` on Bybit's Safe, `0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4`.
* `to` is `0x96221423681A6d52E184D440a8eFCEbB105C7242`, `value` 0, `operation` 1 (delegatecall),
  `safeTxGas` 45746, and `baseGas`, `gasPrice`, `gasToken` and `refundReceiver` all zero.
* `data` is `transfer(0xbDd077f651EBe7f7b3cE16fe5F2b025BE2969516, 0)`.
* `signatures` holds three 65-byte owner signatures.
* The contract at `0x9622...7242` has a single function, `transfer(address,uint256)`, which stores
  its first argument in storage slot 0. A Safe proxy keeps the address of its implementation in
  slot 0. As of November 2026 the Safe's slot 0 holds `0xbDd077f651EBe7f7b3cE16fe5F2b025BE2969516`.

Guard decodes the call into a `safe_exec` action and, because `operation` is 1, a `delegatecall`
action. It never reads the data as an ERC-20 transfer of zero tokens, which is what it looked like.
With the demo policy, the decision is:

> **deny** (`no-delegatecall`): bybit-cold-safe (0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4) would
> delegatecall 0x96221423681A6d52E184D440a8eFCEbB105C7242, which runs that contract's code inside
> the wallet with full access to its storage, balance and permissions. Its call data reads as
> transfer(address,uint256), a name that means nothing under delegatecall.
> 0x96221423681A6d52E184D440a8eFCEbB105C7242 is not an allowed delegatecall target.

The same decision comes out whether Guard sees the `execTransaction` call or the `SafeTx` typed
data the owners sign, because both decode to the same actions. Asked instead to sign the SafeTx
hash itself, as a bare 32-byte message or through the Safe's `approveHash`, Guard denies it under
`no-blind-signing`, because a Safe accepts either as an owner's approval. Approvals cannot release
any of these denials.

## Worked example: Binance, 29 October 2026

Transaction `0xad99264d5532ae0dd04b2004eebda05f25657e70dacb764f7ac8d4ccd051298b`, also stored in
`tests/guard/fixtures/`. By Elliptic's estimate about $6.4 billion left Binance that day, across
several chains. This transaction is how the attacker took over Binance 14,
`0x28C6c06298d514Db089934071355E5743bf21d60`, one of Binance's Ethereum hot wallets. Read from the
chain, and checked with Guard's own code:

* It is a type 4 transaction in block 26,300,753, at 20:20:59 UTC. Its fields hash to the
  transaction hash, and its signature recovers to the account that sent it,
  `0x7C8601d4645b93cea45F71e118A729332e6B5CA0`. It calls Binance 14 with no data and no value.
* Its authorization list holds 50 authorizations. Every one is for chain 1, every one delegates to
  `0x63e842B054Bd624a465454D533C33F573e9c6fAe`, and every one recovers to Binance 14: they were
  signed with Binance 14's own key. That contract had been deployed by the same sending account at
  15:59:11 UTC the same day.
* The authorizations carry consecutive nonces from 18914824 down to 18914775. A busy hot wallet's
  nonce moves all the time, so 50 consecutive nonces made sure one would match whenever the
  transaction was mined, and listing them from the highest down meant no second one could match
  after it. Binance 14 sent its own transaction with nonce 18914808 earlier in the same block, so
  the authorization with nonce 18914809 took effect and the other 49 were skipped. After the block,
  the wallet's nonce was 18914810 and its code was the delegation indicator `0xef0100` followed by
  the contract's address.
* At 20:24:23 UTC the same account called `sweep(address,address[])` (selector `0x8a179be4`) on
  Binance 14, in transaction `0x53bd38b816c23e74134786c29a8c18d792777998a3b9326047b583100e88e2f3`,
  with `0x5227f603C1eE34CE3C06eFe6477003BDfCCB2f8c` and a list of 65 token addresses. Running the
  delegated code, the wallet sent about 167,464 ETH and 63 different ERC-20 tokens to that address.
  No Binance key signed that transaction.

Each authorization reached Binance's signing path as a request to sign
`0x05 || rlp([1, 0x63e842B054Bd624a465454D533C33F573e9c6fAe, nonce])` with Binance 14's key, the
`evm_message` request Guard reads. For the one that took effect, the decision is:

> **deny** (`no-eip7702-delegation`): Authorizes an EIP-7702 delegation: binance-14
> (0x28C6c06298d514Db089934071355E5743bf21d60) would run the code of
> 0x63e842B054Bd624a465454D533C33F573e9c6fAe on chain 1 (authorization nonce 18914809). Every call
> to the wallet would then run that contract's code with the wallet's balance and permissions until
> the delegation is revoked on-chain, and pausing withdrawals would not stop it. Custody wallets
> never delegate.

The other 49 get the same answer, and so would the transaction itself had a custody wallet been
asked to send it, because Guard recovers and refuses every authorization in a type 4 transaction.
The sweep shows why the refusal has to come before signing: once the delegation is on-chain the
attacker no longer needs the signer, and what is left is to notice the outflow. That is the pattern
Watch's `delegated-code-outflow` rule looks for: value leaving an ordinary account from inside
contract code.

## Commands

```
goldenhour guard keygen guard.pem                      # Ed25519 key for the guard or an approver
goldenhour guard add-intent intents.json --ledger ledger.db
goldenhour guard check request.json --policy policies/default.yaml --ledger ledger.db \
    --log audit.jsonl --key guard.pem [--json] [--now 2026-11-01T12:00:00Z]
goldenhour guard approve request.json --key alice.pem --approver alice [--write]
goldenhour guard verify-log audit.jsonl --public-key guard.pem.pub [--head <hash>]
```

`check` without `--ledger` uses an empty ledger, so every transfer is denied for want of an
intent. A request that does not validate is denied as `well-formed`, and logged if `--log` is set.
`verify-log --head` takes any head published earlier, not only the newest.

## Running as a service

The self-hosted server answers `POST /v1/guard/evaluate` with
`goldenhour.guard.service.evaluate_json`, which takes the request as parsed JSON and returns
exactly what `check --json` prints. It never raises for a bad request: a request it cannot read is
denied and logged. It reads its setup from the environment:

* `GOLDENHOUR_GUARD_POLICY`: the policy file. Without it, the shipped `policies/default.yaml`,
  which denies everything until wallets are listed.
* `GOLDENHOUR_STATE`: the state directory (default `state`), which holds the ledger,
  `guard-ledger.db`, and the audit log, `guard-audit.jsonl`.
* `GOLDENHOUR_GUARD_KEY`: the guard's Ed25519 seed as 64 hex digits. Without it, a key is created
  on first use at `guard-key.pem` in the state directory, readable only by its owner, with the
  public key for `verify-log` beside it in `guard-key.pem.pub`.

From Python, `goldenhour.guard.engine.evaluate` decides without side effects, and
`goldenhour.guard.engine.Guard` adds the ledger commit and the audit log.
`goldenhour.guard.cases.build_cases()` builds the public `api/v1/guard.json` document from the
copies of the Bybit and Binance transactions shipped with the package, and checks each one against
its hash. Pass `fetch=fetch_transaction` to read them from `https://ethereum-rpc.publicnode.com`
instead, with Blockscout's JSON-RPC endpoint as a fallback.
