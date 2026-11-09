"""The public demonstration cases: what Guard decides about two real attacks and four routine requests.

`build_cases()` produces the document published as `api/v1/guard.json`. Every decision and reason
in it comes from running Guard on the request shown, with the demo policy shown, so the page can
never claim a decision the code does not make.

The Bybit and Binance cases start from the real transactions, fetched from public Ethereum nodes
and checked before use: their fields must hash to the transaction hash, and they must be what the
case says they are. The other cases run in a demonstration environment whose keys are derived from
the seeds below, which are public on purpose: they let anyone reproduce the signatures, and they
make the keys worthless for anything else.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from goldenhour.guard import keys, secp256k1
from goldenhour.guard.decode import SafeExec, decode
from goldenhour.guard.digest import approval_message, request_digest
from goldenhour.guard.engine import evaluate
from goldenhour.guard.evm import address_to_bytes, keccak256, selector, to_checksum_address
from goldenhour.guard.ledger import Intent, MemoryLedger
from goldenhour.guard.policy import POLICIES_DIR, Policy, load_policy
from goldenhour.guard.request import SigningRequest
from goldenhour.guard.transaction import Signature, authority, authorization_preimage, signed_payload

# Public nodes, tried in order. Both return the standard JSON-RPC transaction object, and every
# transaction is checked against its own hash, so it does not matter which one answers.
RPC_URLS = ("https://ethereum-rpc.publicnode.com", "https://eth.blockscout.com/api/eth-rpc")

BYBIT_TX = "0x46deef0f52e3a983b67abf4714448a41dd7ffd6d32d32da69d62081c68ad7882"
BYBIT_SAFE = "0x1db92e2eebc8e0c075a02bea49a2935bcd2dfcf4"
BYBIT_DELEGATE_TARGET = "0x96221423681a6d52e184d440a8efcebb105c7242"

BINANCE_TX = "0xad99264d5532ae0dd04b2004eebda05f25657e70dacb764f7ac8d4ccd051298b"
BINANCE_14 = "0x28c6c06298d514db089934071355e5743bf21d60"
BINANCE_DELEGATE = "0x63e842b054bd624a465454d533c33f573e9c6fae"
# The one authorization of the fifty that matched Binance 14's nonce when the transaction ran:
# the wallet sent its own transaction with nonce 18914808 earlier in the same block, and after the
# block its nonce was 18914810 and its code was the delegation. The authorizations are listed from
# the highest nonce down, so no second one could match after this one.
BINANCE_EFFECTIVE_NONCE = 18_914_809

DEMO_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
UNKNOWN_SPENDER = "0xbad0000000000000000000000000000000000001"
PRICES = {"prices_usd": {"ETH": "3400", "USDC": "1"}}
GWEI = 10**9


def demo_private_key(label: str) -> int:
    """A secp256k1 key from a public seed. For demonstrations only: anyone can derive it."""
    return int.from_bytes(hashlib.sha256(f"goldenhour guard demo {label}".encode()).digest(), "big") % secp256k1.N


def demo_address(label: str) -> str:
    return secp256k1.private_key_to_address(demo_private_key(label))


def demo_approver(name: str) -> Ed25519PrivateKey:
    """An approver's Ed25519 key from a public seed, matching the public keys in policies/demo.yaml."""
    return keys.from_seed(hashlib.sha256(f"goldenhour guard demo approver {name}".encode()).digest())


HOT = demo_address("hot wallet")
TREASURY = demo_address("treasury wallet")
CUSTOMER = demo_address("customer")
COUNTERPARTY = demo_address("new counterparty")


STORED = {
    BYBIT_TX: "bybit-2025-02-21.json",
    BINANCE_TX: "binance-2026-10-29-delegation.json",
}


def stored_transaction(tx_hash: str) -> dict[str, Any]:
    """A case transaction from the copy shipped with the package, so the demo builds offline.

    The copies are not trusted blindly: the case builders re-hash every field and refuse a
    transaction whose fields do not hash to its known hash. Unknown hashes go to the network.
    """
    name = STORED.get(tx_hash)
    if name:
        path = Path(__file__).with_name("data") / name
        if path.exists():
            return json.loads(path.read_text())
    return fetch_transaction(tx_hash)


def fetch_transaction(tx_hash: str) -> dict[str, Any]:
    """`eth_getTransactionByHash` from the first public node that has the transaction."""
    from goldenhour.http import USER_AGENT

    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionByHash", "params": [tx_hash]}
    for url in RPC_URLS:
        response = httpx.post(url, json=payload, headers={"User-Agent": USER_AGENT}, timeout=30.0)
        response.raise_for_status()
        result = response.json().get("result")
        if isinstance(result, dict):
            return result
    raise ValueError(f"no public node returned transaction {tx_hash}")


def bybit_request(tx: dict[str, Any]) -> SigningRequest:
    """The Bybit transaction as a request from its Safe, after checking it is what the case claims."""
    if tx.get("hash") != BYBIT_TX or tx.get("type") != "0x0":
        raise ValueError("not the legacy transaction 0x46deef0f... that Bybit's Safe executed")
    request = SigningRequest.model_validate(
        {
            "kind": "evm_tx",
            "chain_id": tx["chainId"],
            "wallet": BYBIT_SAFE,
            "type": 0,
            "nonce": tx["nonce"],
            "gas_price": tx["gasPrice"],
            "gas": tx["gas"],
            "to": tx["to"],
            "value": tx["value"],
            "data": tx["input"],
        }
    )
    v = int(tx["v"], 16)
    signature = Signature(v - 35 - 2 * request.chain_id, int(tx["r"], 16), int(tx["s"], 16))
    if "0x" + keccak256(signed_payload(request, signature)).hex() != BYBIT_TX:
        raise ValueError("the fetched fields do not hash to the Bybit transaction hash")
    if request.to != BYBIT_SAFE:
        raise ValueError(f"the transaction calls {request.to}, not Bybit's Safe")
    executed = [a for a in decode(request) if isinstance(a, SafeExec)]
    if not executed or executed[0].operation != 1 or executed[0].to != BYBIT_DELEGATE_TARGET:
        raise ValueError("the transaction is not an execTransaction with operation 1 to the attacker's contract")
    return request


def binance_request(tx: dict[str, Any]) -> SigningRequest:
    """The authorization Binance 14 signed that took effect, after checking all fifty in the transaction.

    The transaction was sent by the attacker's own account, so Binance's signer never saw it. What
    Binance's signer did see, fifty times, was a request to sign `0x05 || rlp([1, delegate, nonce])`
    for Binance 14. That request is what Guard would have been asked to approve.
    """
    if tx.get("hash") != BINANCE_TX or tx.get("type") != "0x4":
        raise ValueError("not the type 4 transaction 0xad99264d... that delegated Binance 14")
    request = SigningRequest.model_validate(
        {
            "kind": "evm_tx",
            "chain_id": tx["chainId"],
            "wallet": tx["from"],
            "type": 4,
            "nonce": tx["nonce"],
            "gas": tx["gas"],
            "max_fee_per_gas": tx["maxFeePerGas"],
            "max_priority_fee_per_gas": tx["maxPriorityFeePerGas"],
            "to": tx["to"],
            "value": tx["value"],
            "data": tx["input"],
            "access_list": [{"address": e["address"], "storage_keys": e["storageKeys"]} for e in tx["accessList"]],
            "authorization_list": [
                {
                    "chain_id": a["chainId"],
                    "address": a["address"],
                    "nonce": a["nonce"],
                    "y_parity": a["yParity"],
                    "r": a["r"],
                    "s": a["s"],
                }
                for a in tx["authorizationList"]
            ],
        }
    )
    signature = Signature(int(tx["yParity"], 16), int(tx["r"], 16), int(tx["s"], 16))
    if "0x" + keccak256(signed_payload(request, signature)).hex() != BINANCE_TX:
        raise ValueError("the fetched fields do not hash to the Binance transaction hash")
    entries = request.authorization_list
    if request.to != BINANCE_14 or len(entries) != 50:
        raise ValueError("the transaction is not fifty authorizations sent to Binance 14")
    for entry in entries:
        if (entry.chain_id, entry.address, authority(entry)) != (1, BINANCE_DELEGATE, BINANCE_14):
            raise ValueError(
                f"authorization with nonce {entry.nonce} is not Binance 14 delegating to {BINANCE_DELEGATE}"
            )
    if BINANCE_EFFECTIVE_NONCE not in {entry.nonce for entry in entries}:
        raise ValueError(f"no authorization carries nonce {BINANCE_EFFECTIVE_NONCE}")
    message = authorization_preimage(1, BINANCE_DELEGATE, BINANCE_EFFECTIVE_NONCE)
    return SigningRequest.model_validate(
        {"kind": "evm_message", "chain_id": 1, "wallet": BINANCE_14, "message": "0x" + message.hex()}
    )


def _call(signature: str, *args: str | int) -> str:
    """Call data for a function whose arguments are all addresses or uints."""
    words = [address_to_bytes(a).rjust(32, b"\x00") if isinstance(a, str) else a.to_bytes(32, "big") for a in args]
    return "0x" + (selector(signature) + b"".join(words)).hex()


def _tx(nonce: int, to: str, *, value: int = 0, data: str = "0x", gas: int = 21_000, **extra: Any) -> dict[str, Any]:
    """A type 2 transaction on Ethereum, as the demo exchange's signing service would receive it."""
    return {
        "kind": "evm_tx",
        "chain_id": 1,
        "type": 2,
        "nonce": nonce,
        "to": to,
        "value": str(value),
        "data": data,
        "gas": gas,
        "max_fee_per_gas": str(30 * GWEI),
        "max_priority_fee_per_gas": str(GWEI),
        "context": PRICES,
        **extra,
    }


def _withdrawal(intent_id: str | None) -> SigningRequest:
    return SigningRequest.model_validate(_tx(7_311, CUSTOMER, value=25 * 10**17, wallet=HOT, intent_id=intent_id))


def _treasury_move(approvers: tuple[str, ...]) -> SigningRequest:
    body = _tx(
        902,
        USDC,
        data=_call("transfer(address,uint256)", COUNTERPARTY, 4_000_000 * 10**6),
        gas=65_000,
        wallet=TREASURY,
        intent_id="tr-20261101-0007",
    )
    message = approval_message(request_digest(SigningRequest.model_validate(body)))
    approvals = [{"approver": name, "signature": demo_approver(name).sign(message).hex()} for name in approvers]
    return SigningRequest.model_validate({**body, "approvals": approvals})


def demo_intents() -> list[Intent]:
    return [
        Intent(
            id="wd-20261101-0042",
            wallet=HOT,
            chain_id=1,
            asset="native",
            destination=CUSTOMER,
            amount=25 * 10**17,
            expires_at=DEMO_NOW + timedelta(hours=1),
            note="customer withdrawal",
        ),
        Intent(
            id="tr-20261101-0007",
            wallet=TREASURY,
            chain_id=1,
            asset=USDC,
            destination=COUNTERPARTY,
            amount=4_000_000 * 10**6,
            expires_at=DEMO_NOW + timedelta(hours=6),
            note="settlement with a new counterparty",
        ),
    ]


def _case(
    policy: Policy, request: SigningRequest, tx: str | None, *, case_id: str, title: str, story: str
) -> dict[str, Any]:
    """Decide `request` against a fresh demo ledger, so each case stands on its own."""
    decision = evaluate(request, policy, MemoryLedger(demo_intents()), DEMO_NOW)
    return {
        "id": case_id,
        "title": title,
        "story": story,
        "chain": "ethereum",
        "tx": tx,
        "request": request.to_json(),
        "decision": decision.outcome,
        "reasons": decision.reasons(),
    }


def build_cases(fetch: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """The `api/v1/guard.json` document, from the shipped copies of the real transactions unless
    `fetch` is replaced. Either way every transaction is re-hashed and checked before use."""
    fetch = fetch or stored_transaction
    policy_yaml = (POLICIES_DIR / "demo.yaml").read_text(encoding="utf-8")
    policy = load_policy(policy_yaml)
    safe = to_checksum_address(BYBIT_SAFE)
    cases = [
        _case(
            policy,
            bybit_request(fetch(BYBIT_TX)),
            BYBIT_TX,
            case_id="bybit-2025-02-21",
            title="Bybit: a delegatecall dressed as a transfer",
            story=(
                f"On 21 February 2025 Bybit's Ethereum cold wallet, the Safe {safe}, executed this transaction. "
                "Malicious code injected into the Safe{Wallet} web interface had changed the transaction its "
                "signers were asked to sign, and three owners signed an execTransaction with operation 1, a "
                "delegatecall, to 0x96221423681A6d52E184D440a8eFCEbB105C7242. That contract's only function "
                "stores its first argument in storage slot 0, where a Safe proxy keeps the address of its "
                "implementation, so the Safe was rewired to code the attacker controlled and about $1.5 billion "
                "was drained. Guard reads the operation field, sees a delegatecall to a contract that is not an "
                "allowed target, and refuses to sign."
            ),
        ),
        _case(
            policy,
            binance_request(fetch(BINANCE_TX)),
            BINANCE_TX,
            case_id="binance-2026-10-29",
            title="Binance: fifty delegations in one transaction",
            story=(
                "At 20:20:59 UTC on 29 October 2026 this transaction put an EIP-7702 delegation on Binance 14 "
                f"({to_checksum_address(BINANCE_14)}), a Binance hot wallet. An attacker's account sent it, but "
                "its authorization list carried 50 authorizations signed with Binance 14's own key, all "
                f"delegating the wallet to {to_checksum_address(BINANCE_DELEGATE)}, a contract the same account "
                "had deployed at 15:59 UTC. They used consecutive nonces, 18914775 to 18914824, so that one "
                "would match the busy wallet's nonce whenever the transaction was mined: 18914809 did, and the "
                "other 49 were skipped. At 20:24:23 UTC the attacker's account called sweep(address,address[]) "
                "on Binance 14, and the wallet, now running the delegated code, sent about 167,464 ETH and 63 "
                "different tokens out in one transaction that no Binance key signed. Each of the 50 "
                "authorizations was a request to sign with Binance 14's key. The one shown here is the one that "
                "took effect, and Guard's no-eip7702-delegation rule refuses all 50 before anything is signed."
            ),
        ),
        _case(
            policy,
            _withdrawal("wd-20261101-0042"),
            None,
            case_id="withdrawal-with-intent",
            title="A withdrawal with a matching intent",
            story=(
                "A routine withdrawal: the exchange hot wallet sends 2.5 ETH to a customer, and the withdrawal "
                "system has recorded intent wd-20261101-0042 for exactly that payment. Every rule passes, so "
                "Guard allows the request and consumes the intent, which can never pay twice."
            ),
        ),
        _case(
            policy,
            _withdrawal(None),
            None,
            case_id="withdrawal-without-intent",
            title="The same withdrawal without an intent",
            story=(
                "The same transfer with no intent behind it. This is what a compromised caller of the signing "
                "service looks like from the signer's side: a well-formed payment the withdrawal system never "
                "asked for. Guard denies it."
            ),
        ),
        _case(
            policy,
            SigningRequest.model_validate(
                _tx(
                    7_312,
                    USDC,
                    data=_call("approve(address,uint256)", UNKNOWN_SPENDER, 2**256 - 1),
                    gas=60_000,
                    wallet=HOT,
                )
            ),
            None,
            case_id="unlimited-approval",
            title="An unlimited approval to an unknown spender",
            story=(
                "The hot wallet is asked to let an unknown address spend an unlimited amount of its USDC, the "
                "standing permission a drainer needs. Guard denies it twice over: the amount is unlimited, and "
                "the spender is not on the allowlist."
            ),
        ),
        _case(
            policy,
            _treasury_move(()),
            None,
            case_id="treasury-new-destination",
            title="A large treasury move to a new destination",
            story=(
                "The treasury wallet is asked to send 4,000,000 USDC to a counterparty it has never paid. The "
                "withdrawal system has an intent for the payment, but the destination is new and the amount is "
                "above the treasury's limit for one request, so Guard holds it for the approvers."
            ),
        ),
        _case(
            policy,
            _treasury_move(("alice", "bob")),
            None,
            case_id="treasury-new-destination-approved",
            title="The same move with two of three approvals",
            story=(
                "The same request after two of the three approvers, alice and bob, have signed its request "
                "digest with their Ed25519 keys. The quorum releases the hold, Guard allows the payment and "
                "consumes the intent. No number of approvals could have released a deny."
            ),
        ),
    ]
    return {"policy_yaml": policy_yaml, "cases": cases}
