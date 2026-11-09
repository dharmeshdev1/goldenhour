"""Solana through a public JSON-RPC node.

Solana keeps tokens in token accounts that belong to owner wallets, so a token transfer names two
token accounts, not two people. The transaction's own token balance records, and the instructions
that create token accounts, say which wallet owns each account; transfers are rewritten to run
between those owners, so the trail follows wallets the way it does on every other chain.
Instructions nested inside program calls (a swap routed through three pools, say) are read too,
because that is where most of the value moves.

SOL is counted at the owner: a wallet's own account plus the lamports in its token accounts. So
wrapped SOL is SOL, wrapping and unwrapping are not movements, and creating or closing an account
moves lamports only when another owner pays or is paid. Each transaction is then reconciled with
its metadata, which states every account's lamports before and after: SOL that the parsed
instructions do not explain (a program paying a wallet directly, say) is booked against
`solana:@program`, so each wallet's ledger matches the chain to the lamport.
"""

from __future__ import annotations

import os
from typing import cast

from goldenhour.http import PoliteClient
from goldenhour.models import Chain, Kind, Page, Token, Transfer, fee_node, native_asset, node_id, token_asset

RPC = "https://api.mainnet-beta.solana.com"
MAX_TX_VERSION = 1  # the highest transaction version this parser has been checked against
MAX_TX_TRIES = 5  # calls that may fail to serve one transaction before it is skipped
ASSET = native_asset(Chain.SOLANA)
WSOL = "So11111111111111111111111111111111111111112"


def _flatten(tx: dict) -> list[dict]:
    """Outer instructions in order, each followed by the inner instructions it caused."""
    message = (tx.get("transaction") or {}).get("message") or {}
    inner = {
        block["index"]: block.get("instructions") or []
        for block in (tx.get("meta") or {}).get("innerInstructions") or []
    }
    flat: list[dict] = []
    for i, ix in enumerate(message.get("instructions") or []):
        flat.append(ix)
        flat.extend(inner.get(i, []))
    return flat


PROGRAM = "solana:@program"  # lamports a program paid or took directly, without an instruction we parse
SYSTEM_MOVES = {
    # instruction type -> (field naming the account debited, field naming the account credited)
    "transfer": ("source", "destination"),
    "transferWithSeed": ("source", "destination"),
    "createAccount": ("source", "newAccount"),
    "createAccountWithSeed": ("source", "newAccount"),
    "withdrawNonceAccount": ("nonceAccount", "destination"),
}


def _owners(tx: dict, keys: list[str]) -> dict[str, tuple[str, str, int]]:
    """Token account -> (owner, mint, decimals), from the balance records and from the
    instructions that create token accounts (an account opened and closed inside one transaction
    appears in neither balance record)."""
    meta = tx.get("meta") or {}
    owners: dict[str, tuple[str, str, int]] = {}
    for bal in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
        idx = bal.get("accountIndex")
        if idx is None or idx >= len(keys) or not bal.get("owner"):
            continue
        decimals = int((bal.get("uiTokenAmount") or {}).get("decimals") or 0)
        owners[keys[idx]] = (bal["owner"], bal.get("mint", ""), decimals)
    for ix in _flatten(tx):
        parsed = ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info = parsed.get("info") or {}
        kind = parsed.get("type", "")
        if kind in ("initializeAccount", "initializeAccount2", "initializeAccount3") and info.get("owner"):
            owners.setdefault(info.get("account", ""), (info["owner"], info.get("mint", ""), 0))
        elif (
            ix.get("program") == "spl-associated-token-account"
            and kind in ("create", "createIdempotent")
            and info.get("wallet")
            and info.get("account")
        ):
            owners.setdefault(info["account"], (info["wallet"], info.get("mint", ""), 0))
    owners.pop("", None)
    return owners


def tx_transfers(tx: dict, signature: str, subject: str) -> tuple[list[Transfer], list[Token]]:
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return _fee(tx, signature, subject)
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in message.get("accountKeys") or []]
    slot = int(tx.get("slot") or 0)
    ts = int(tx.get("blockTime") or 0)
    owners = _owners(tx, keys)
    pre = meta.get("preBalances") or []
    # Lamports per account as the instructions run, to know what closing an account pays out.
    lamports = {k: int(pre[i]) for i, k in enumerate(keys) if i < len(pre)}

    def holder(account: str) -> str:
        return owners[account][0] if account in owners else account

    out: list[Transfer] = []
    tokens: dict[str, Token] = {}

    def move(k: int, src: str, dst: str, amount: int, asset: str, kind: Kind) -> None:
        sender, receiver = holder(src), holder(dst)
        if amount <= 0 or not sender or not receiver or sender == receiver:
            return  # wrapping, unwrapping and paying one's own token account are not movements
        out.append(
            Transfer(
                uid=f"solana:{signature}:{k}",
                chain=Chain.SOLANA,
                tx=signature,
                block=slot,
                ts=ts,
                position=k,
                sender=node_id(Chain.SOLANA, sender),
                receiver=node_id(Chain.SOLANA, receiver),
                asset=asset,
                amount=amount,
                kind=kind,
            )
        )

    for k, ix in enumerate(_flatten(tx)):
        parsed = ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        kind = parsed.get("type", "")
        info = parsed.get("info") or {}
        program = ix.get("program", "")
        if program == "system" and kind in SYSTEM_MOVES:
            debit, credit = SYSTEM_MOVES[kind]
            src, dst, amount = info.get(debit, ""), info.get(credit, ""), int(info.get("lamports") or 0)
            lamports[src] = lamports.get(src, 0) - amount
            lamports[dst] = lamports.get(dst, 0) + amount
            move(k, src, dst, amount, ASSET, Kind.NATIVE)
        elif program == "stake" and kind == "withdraw":
            src, dst, amount = info.get("stakeAccount", ""), info.get("destination", ""), int(info.get("lamports") or 0)
            lamports[src] = lamports.get(src, 0) - amount
            lamports[dst] = lamports.get(dst, 0) + amount
            move(k, src, dst, amount, ASSET, Kind.NATIVE)
        elif program in ("spl-token", "spl-token-2022") and kind == "closeAccount":
            src, dst = info.get("account", ""), info.get("destination", "")
            amount = max(lamports.get(src, 0), 0)
            lamports[src] = 0
            lamports[dst] = lamports.get(dst, 0) + amount
            move(k, src, dst, amount, ASSET, Kind.NATIVE)
        elif program in ("spl-token", "spl-token-2022") and kind in ("transfer", "transferChecked"):
            src_acct, dst_acct = info.get("source", ""), info.get("destination", "")
            src, dst = owners.get(src_acct), owners.get(dst_acct)
            mint = info.get("mint") or (src or dst or ("", "", 0))[1]
            amount = int((info.get("tokenAmount") or {}).get("amount") or info.get("amount") or 0)
            if not mint or amount <= 0:
                continue
            if mint == WSOL:
                # Wrapped SOL moves lamports along with the token amount: it is SOL.
                lamports[src_acct] = lamports.get(src_acct, 0) - amount
                lamports[dst_acct] = lamports.get(dst_acct, 0) + amount
                sender = src[0] if src else (info.get("authority") or info.get("multisigAuthority") or src_acct)
                move(k, sender, dst_acct, amount, ASSET, Kind.NATIVE)
                continue
            sender = src[0] if src else (info.get("authority") or info.get("multisigAuthority") or src_acct)
            receiver = dst[0] if dst else dst_acct
            decimals = int((info.get("tokenAmount") or {}).get("decimals") or (src or dst or ("", "", 0))[2])
            asset = token_asset(Chain.SOLANA, mint)
            tokens[asset] = Token(asset=asset, symbol="", decimals=decimals)
            move(k, sender, receiver, amount, asset, Kind.TOKEN)
    fees, _ = _fee(tx, signature, subject)
    out.extend(fees)
    _reconcile(out, tx, keys, owners, signature, subject)
    return out, list(tokens.values())


def _fee(tx: dict, signature: str, subject: str) -> tuple[list[Transfer], list[Token]]:
    """The fee, which the first signer pays even when the transaction fails."""
    meta = tx.get("meta") or {}
    message = (tx.get("transaction") or {}).get("message") or {}
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in message.get("accountKeys") or []]
    fee = int(meta.get("fee") or 0)
    if fee <= 0 or not keys or keys[0] != subject:
        return [], []
    return [
        Transfer(
            uid=f"solana:{signature}:f",
            chain=Chain.SOLANA,
            tx=signature,
            block=int(tx.get("slot") or 0),
            ts=int(tx.get("blockTime") or 0),
            position=1_000_000,
            sender=node_id(Chain.SOLANA, keys[0]),
            receiver=fee_node(Chain.SOLANA),
            asset=ASSET,
            amount=fee,
            kind=Kind.FEE,
        )
    ], []


Owners = dict[str, tuple[str, str, int]]


def _reconcile(out: list[Transfer], tx: dict, keys: list[str], owners: Owners, signature: str, subject: str) -> None:
    """Book the subject's SOL that the parsed instructions do not explain, from the metadata."""
    meta = tx.get("meta") or {}
    pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
    if len(pre) != len(keys) or len(post) != len(keys):
        return
    mine = {i for i, k in enumerate(keys) if k == subject or (k in owners and owners[k][0] == subject)}
    change = sum(int(post[i]) - int(pre[i]) for i in mine)
    me = node_id(Chain.SOLANA, subject)
    booked = sum(t.amount for t in out if t.asset == ASSET and t.receiver == me) - sum(
        t.amount for t in out if t.asset == ASSET and t.sender == me
    )
    rest = change - booked
    if rest == 0:
        return
    sender, receiver = (me, PROGRAM) if rest < 0 else (PROGRAM, me)
    out.append(
        Transfer(
            uid=f"solana:{signature}:r:{subject}",
            chain=Chain.SOLANA,
            tx=signature,
            block=int(tx.get("slot") or 0),
            ts=int(tx.get("blockTime") or 0),
            position=999_999,
            sender=sender,
            receiver=receiver,
            asset=ASSET,
            amount=abs(rest),
            kind=Kind.NATIVE,
        )
    )


class SolanaAdapter:
    chain = Chain.SOLANA

    def __init__(self, client: PoliteClient, url: str | None = None) -> None:
        self.client = client
        self.url = url or os.environ.get("SOLANA_RPC_URL") or RPC

    async def _rpc(self, method: str, params: list) -> object:
        data = await self.client.post_json(self.url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if not isinstance(data, dict) or "error" in data:
            raise RuntimeError(f"{method}: {data.get('error') if isinstance(data, dict) else data}")
        return data.get("result")

    async def signatures(self, address: str, until: str | None = None, limit: int = 20_000) -> list[dict]:
        """Signatures newest first, down to (not including) `until`."""
        found: list[dict] = []
        before: str | None = None
        while len(found) < limit:
            opts: dict[str, object] = {"limit": 1000}
            if before:
                opts["before"] = before
            if until:
                opts["until"] = until
            batch = await self._rpc("getSignaturesForAddress", [address, opts])
            if not isinstance(batch, list) or not batch:
                break
            found.extend(batch)
            before = batch[-1]["signature"]
            if len(batch) < 1000:
                break
        return found

    async def history(self, address: str, cursor: dict | None = None, max_txs: int = 5_000, chunk: int = 150) -> Page:
        """The address's transactions since the cursor, oldest first, at most `chunk` per call.

        Public Solana nodes are slow to serve full transactions, and a busy address can have
        thousands. Reading oldest first and moving the cursor after each chunk means a slow node
        delays an address instead of stalling the run, and no work is lost between calls. The
        cursor's `more` flag says another call is needed.
        """
        cursor = dict(cursor or {})
        sigs = await self.signatures(address, until=cursor.get("newest"))
        page = Page(cursor=cursor)
        ok = [s for s in sigs if s.get("err") is None]
        if len(ok) + int(cursor.get("read", 0)) > max_txs:
            page.complete = False
            page.cursor["hub"] = True
            return page
        oldest_first = list(reversed(sigs))
        batch = oldest_first[:chunk]
        failures: dict[str, int] = dict(cursor.get("failures") or {})
        done = 0
        for s in batch:
            if s.get("err") is None:
                sig = s["signature"]
                try:
                    tx = await self._rpc(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": MAX_TX_VERSION}],
                    )
                except RuntimeError:
                    tx = None
                if not isinstance(tx, dict):
                    # Not served this time (a lagging node, a rate limit). The cursor stops before it
                    # so the next call asks again; only after MAX_TX_TRIES refusals is it skipped,
                    # and then it is counted and named rather than lost silently.
                    failures[sig] = failures.get(sig, 0) + 1
                    if failures[sig] < MAX_TX_TRIES:
                        break
                    page.cursor["skipped"] = int(cast(int, page.cursor.get("skipped", 0))) + 1
                    page.cursor["skipped_sigs"] = [*cast(list[str], page.cursor.get("skipped_sigs", [])), sig][-20:]
                else:
                    transfers, tokens = tx_transfers(tx, sig, address)
                    page.transfers.extend(transfers)
                    for token in tokens:
                        page.tokens[token.asset] = token
                failures.pop(sig, None)
            done += 1
        if done:
            page.cursor["newest"] = batch[done - 1]["signature"]
            page.cursor["read"] = int(cursor.get("read", 0)) + done
        page.cursor["failures"] = failures
        page.cursor["more"] = len(oldest_first) > done
        return page

    async def balance(self, address: str) -> int:
        result = await self._rpc("getBalance", [address])
        return int((result or {}).get("value", 0)) if isinstance(result, dict) else 0
