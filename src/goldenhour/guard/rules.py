"""The rules. Each one reads the decoded actions and returns findings; none of them changes anything.

Every finding says what the request would do and why that matters, in words an approver can act
on, so a held request can be judged from its reasons alone. Addresses are printed in full with
their policy label, because a shortened address is exactly what an attacker can imitate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from goldenhour.guard.decode import (
    Action,
    AdminChange,
    Approval,
    BlindSignature,
    ContractCreation,
    DelegateCall,
    Delegation,
    PersonalMessage,
    ProxyUpgrade,
    SafeChange,
    SafeGasRefund,
    Transfer,
    UnknownCall,
    UnknownTypedData,
)
from goldenhour.guard.evm import NATIVE, ZERO_ADDRESS, to_checksum_address
from goldenhour.guard.ledger import Intent, LedgerView
from goldenhour.guard.policy import Policy
from goldenhour.guard.request import SigningRequest

type Effect = Literal["deny", "hold", "note"]


@dataclass(frozen=True)
class Finding:
    """One rule's verdict on one action. A `note` explains without blocking anything."""

    rule: str
    effect: Effect
    message: str
    path: str | None = None


@dataclass
class Context:
    """Everything a rule may look at, with the facts several rules share computed once."""

    request: SigningRequest
    policy: Policy
    actions: Sequence[Action]
    ledger: LedgerView
    now: datetime
    transfers: list[Transfer] = field(init=False)
    values: list[Decimal | None] = field(init=False)
    intent_problems: list[str | None] = field(init=False)
    matched_intent: str | None = field(init=False)

    def __post_init__(self) -> None:
        self.transfers = [a for a in self.actions if isinstance(a, Transfer)]
        self.values = [self.value_usd(t) for t in self.transfers]
        self.intent_problems, self.matched_intent = self._match_intent()

    def name(self, address: str | None) -> str:
        if address is None:
            return "an unknown account"
        label = self.policy.label(address)
        checksummed = to_checksum_address(address)
        return f"{label} ({checksummed})" if label else checksummed

    def amount(self, asset: str, amount: int) -> str:
        known = self.policy.asset(self.request.chain_id, asset)
        if known is not None:
            units = Decimal(amount).scaleb(-known.decimals).normalize()
            return f"{units:,f} {known.symbol}"
        if asset == NATIVE:
            return f"{amount} base units of the native coin"
        return f"{amount} base units of token {self.name(asset)}"

    def value_usd(self, transfer: Transfer) -> Decimal | None:
        """USD value at the higher of the caller's price and the policy floor; None if Guard cannot tell."""
        asset = self.policy.asset(self.request.chain_id, transfer.asset)
        price = self.request.context.prices_usd.get(asset.symbol) if asset else None
        if asset is None or price is None:
            return None
        return Decimal(transfer.amount).scaleb(-asset.decimals) * max(price, asset.price_floor_usd)

    def grants(self, approval: Approval) -> bool:
        """Whether an approval gives the spender anything.

        Zero clears an ERC-20 allowance, but `approve(spender, 0)` on an ERC-721 contract approves
        token id 0 through the same selector. So zero counts as a revocation only on a contract the
        policy lists as an ERC-20 asset, or when the spender is the zero address. Permits, Permit2
        and `setApprovalForAll(spender, false)` have no such ambiguity.
        """
        if approval.amount != 0:
            return True
        if approval.kind in ("erc20_approve", "erc20_increase_allowance"):
            listed = self.policy.asset(self.request.chain_id, approval.token) is not None
            return not listed and approval.spender != ZERO_ADDRESS
        return False

    def by_source(self) -> dict[str, list[tuple[Transfer, Decimal | None]]]:
        groups: dict[str, list[tuple[Transfer, Decimal | None]]] = {}
        for transfer, value in zip(self.transfers, self.values, strict=True):
            groups.setdefault(transfer.source, []).append((transfer, value))
        return groups

    def _match_intent(self) -> tuple[list[str | None], str | None]:
        """For each transfer, why no intent covers it (None when one does), and the intent matched."""
        intent_id = self.request.intent_id
        intent = self.ledger.intent(intent_id) if intent_id else None
        problems: list[str | None] = []
        matched = False
        for transfer in self.transfers:
            if matched:
                problem: str | None = f"intent {intent_id} already pays an earlier transfer, and one intent pays one"
            else:
                problem = self._intent_problem(intent_id, intent, transfer)
                matched = problem is None
            problems.append(problem)
        return problems, intent_id if matched else None

    def _intent_problem(self, intent_id: str | None, intent: Intent | None, transfer: Transfer) -> str | None:
        if intent_id is None:
            return "the request names no withdrawal intent"
        if intent is None:
            return f"intent {intent_id} is not in the ledger"
        if intent.used_at is not None:
            return f"intent {intent_id} was already used at {_iso(intent.used_at)}"
        if self.now >= intent.expires_at:
            return f"intent {intent_id} expired at {_iso(intent.expires_at)}"
        if intent.wallet != transfer.source:
            return f"intent {intent_id} pays from {self.name(intent.wallet)}"
        if intent.chain_id != self.request.chain_id:
            return f"intent {intent_id} is for chain {intent.chain_id}"
        if intent.asset != transfer.asset:
            return f"intent {intent_id} is for {self.amount(intent.asset, intent.amount)}"
        if intent.destination != transfer.destination:
            return f"intent {intent_id} pays {self.name(intent.destination)}"
        if transfer.amount > intent.amount:
            return f"intent {intent_id} allows at most {self.amount(intent.asset, intent.amount)}"
        return None


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def _window(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{seconds} seconds"


def no_eip7702_delegation(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.no_eip7702_delegation
    findings = []
    for action in ctx.actions:
        if not isinstance(action, Delegation):
            continue
        if action.revokes:
            findings.append(
                Finding(
                    "no-eip7702-delegation",
                    "note",
                    f"Clears the EIP-7702 delegation of {ctx.name(action.authority)}. Revoking is always allowed.",
                    action.path,
                )
            )
            continue
        scope = "on every chain" if action.chain_id == 0 else f"on chain {action.chain_id}"
        signer = ctx.name(action.authority) if action.authority else "an account whose signature does not verify"
        findings.append(
            Finding(
                "no-eip7702-delegation",
                rule.severity,
                f"Authorizes an EIP-7702 delegation: {signer} would run the code of {ctx.name(action.delegate)} "
                f"{scope} (authorization nonce {action.nonce}). Every call to the wallet would then run that "
                "contract's code with the wallet's balance and permissions until the delegation is revoked "
                "on-chain, and pausing withdrawals would not stop it. Custody wallets never delegate.",
                action.path,
            )
        )
    return findings


def no_delegatecall(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.no_delegatecall
    allowed = {entry.address for entry in rule.allow_targets}
    findings = []
    for action in ctx.actions:
        if not isinstance(action, DelegateCall) or action.target in allowed:
            continue
        disguise = (
            f" Its call data reads as {action.function}, a name that means nothing under delegatecall."
            if action.function
            else ""
        )
        findings.append(
            Finding(
                "no-delegatecall",
                rule.severity,
                f"{ctx.name(action.actor)} would delegatecall {ctx.name(action.target)}, which runs that contract's "
                "code inside the wallet with full access to its storage, balance and permissions."
                f"{disguise} {to_checksum_address(action.target)} is not an allowed delegatecall target.",
                action.path,
            )
        )
    return findings


def no_blind_signing(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.no_blind_signing
    findings = []
    for action in ctx.actions:
        if not isinstance(action, BlindSignature):
            continue
        if action.via == "approveHash":
            what = (
                f"{ctx.name(action.actor)} would call approveHash({action.hash}) on Safe {ctx.name(action.safe)}, "
                "approving whatever Safe transaction has that hash without showing it"
            )
        else:
            what = (
                f"Signs the bare 32-byte hash {action.hash} as a message. Guard cannot see what it stands for, "
                "and a Safe accepts such a signature (with 4 added to v) as an owner's approval of the "
                "transaction with that hash"
            )
        message = f"{what}. Guard does not sign what it cannot read."
        findings.append(Finding("no-blind-signing", rule.severity, message, action.path))
    return findings


def _change(ctx: Context, action: SafeChange | ProxyUpgrade | AdminChange) -> str:
    if isinstance(action, AdminChange):
        return (
            f"Hands control of {ctx.name(action.contract)} to {ctx.name(action.new_admin)} ({action.method}), "
            "which then decides how it is upgraded or administered"
        )
    if isinstance(action, ProxyUpgrade):
        init = " and runs initialization code in it" if action.init_data else ""
        return f"Upgrades {ctx.name(action.proxy)} to the implementation {ctx.name(action.implementation)}{init}"
    a, safe = action.args, ctx.name(action.safe)
    match action.method:
        case "addOwnerWithThreshold":
            return f"Adds {ctx.name(a['owner'])} as an owner of Safe {safe}, with threshold {a['threshold']}"
        case "removeOwner":
            return f"Removes owner {ctx.name(a['owner'])} from Safe {safe}, with threshold {a['threshold']}"
        case "swapOwner":
            return f"Replaces owner {ctx.name(a['oldOwner'])} of Safe {safe} with {ctx.name(a['newOwner'])}"
        case "changeThreshold":
            return f"Changes the threshold of Safe {safe} to {a['threshold']}"
        case "enableModule":
            module = ctx.name(a["module"])
            return f"Enables module {module} on Safe {safe}, which could then move funds without owner signatures"
        case "disableModule":
            return f"Disables module {ctx.name(a['module'])} on Safe {safe}"
        case "setGuard":
            return f"Sets the transaction guard of Safe {safe} to {ctx.name(a['guard'])}"
        case _:
            return f"Sets the fallback handler of Safe {safe} to {ctx.name(a['handler'])}"


def no_upgrade_or_owner_change(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.no_upgrade_or_owner_change
    window = ctx.policy.change_window(ctx.now)
    findings = []
    for action in ctx.actions:
        if not isinstance(action, SafeChange | ProxyUpgrade | AdminChange):
            continue
        if window is not None:
            reason = f" for {window.reason}" if window.reason else ""
            effect: Effect = "hold"
            message = (
                f"{_change(ctx, action)}. A change window{reason} is open until {_iso(window.end)}, so this needs "
                "a quorum of approvers."
            )
        else:
            effect = rule.severity
            message = (
                f"{_change(ctx, action)}. Upgrades and changes to who controls a wallet are only possible inside a "
                "scheduled change window, and none is open."
            )
        findings.append(Finding("no-upgrade-or-owner-change", effect, message, action.path))
    return findings


def _approvals(ctx: Context) -> list[Approval]:
    return [a for a in ctx.actions if isinstance(a, Approval) and ctx.grants(a)]


def no_unlimited_approval(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.no_unlimited_approval
    findings = []
    for action in _approvals(ctx):
        if action.amount is not None and action.amount < rule.threshold:
            continue
        if action.amount is None:
            grant = f"move every token it holds in the collection {ctx.name(action.token)}"
        else:
            grant = f"spend an unlimited amount of {ctx.name(action.token)}"
        findings.append(
            Finding(
                "no-unlimited-approval",
                rule.severity,
                f"{ctx.name(action.owner)} would let {ctx.name(action.spender)} {grant}. An unlimited approval "
                "outlives the transaction that needed it and lets the spender drain the wallet whenever it likes.",
                action.path,
            )
        )
    return findings


def approval_allowlist(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.approval_allowlist
    allowed = {entry.address for entry in rule.spenders}
    findings = []
    for action in _approvals(ctx):
        if action.spender in allowed:
            continue
        nft = (
            " The policy does not list this contract as an ERC-20 token, so approve(spender, 0) may approve "
            "the NFT with id 0 rather than revoke anything."
            if action.amount == 0
            else ""
        )
        findings.append(
            Finding(
                "approval-allowlist",
                rule.severity,
                f"{ctx.name(action.spender)} is not an approved spender, so it may not be given an allowance over "
                f"{ctx.name(action.token)}.{nft}",
                action.path,
            )
        )
    return findings


def intent_required(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.intent_required
    findings = []
    for transfer, problem in zip(ctx.transfers, ctx.intent_problems, strict=True):
        moves = (
            f"Moves {ctx.amount(transfer.asset, transfer.amount)} from {ctx.name(transfer.source)} to "
            f"{ctx.name(transfer.destination)}"
        )
        if problem is None:
            intent = ctx.request.intent_id
            message = f"{moves}, matching withdrawal intent {intent}, which is used up if the request is allowed."
            findings.append(Finding("intent-required", "note", message, transfer.path))
        else:
            message = f"{moves}, but {problem}. Every outbound transfer must match an unused withdrawal intent."
            findings.append(Finding("intent-required", rule.severity, message, transfer.path))
    for refund in (a for a in ctx.actions if isinstance(a, SafeGasRefund)):
        receiver = "whoever submits the transaction" if refund.receiver == ZERO_ADDRESS else ctx.name(refund.receiver)
        findings.append(
            Finding(
                "intent-required",
                rule.severity,
                f"Safe {ctx.name(refund.safe)} would pay a gas refund to {receiver} at a gas price of "
                f"{refund.gas_price}. A refund is a payment no withdrawal intent covers.",
                refund.path,
            )
        )
    return findings


def _unvalued(ctx: Context, transfers: list[Transfer], rule_id: str, severity: Effect) -> list[Finding]:
    return [
        Finding(
            rule_id,
            severity,
            f"Guard cannot value {ctx.amount(t.asset, t.amount)}: the asset is not in the policy or the request "
            "carries no USD price for it, so the USD limit cannot be checked.",
            t.path,
        )
        for t in transfers
    ]


def velocity(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.velocity
    since = ctx.now - timedelta(seconds=rule.window)
    findings = []
    for source, items in ctx.by_source().items():
        findings += _unvalued(ctx, [t for t, value in items if value is None], "velocity", rule.severity)
        total = sum((value for _, value in items if value is not None), Decimal(0))
        wallet = ctx.policy.wallet(source)
        limit = wallet.velocity_limit_usd if wallet and wallet.velocity_limit_usd is not None else rule.limit_usd
        earlier = ctx.ledger.spent_usd(source, since, ctx.now)
        if total and earlier + total > limit:
            findings.append(
                Finding(
                    "velocity",
                    rule.severity,
                    f"{ctx.name(source)} would send {_usd(total)} after {_usd(earlier)} in the last "
                    f"{_window(rule.window)}, past its limit of {_usd(limit)} per {_window(rule.window)}.",
                    items[0][0].path,
                )
            )
    return findings


def max_per_tx(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.max_per_tx
    findings = []
    for source, items in ctx.by_source().items():
        findings += _unvalued(ctx, [t for t, value in items if value is None], "max-per-tx", rule.severity)
        total = sum((value for _, value in items if value is not None), Decimal(0))
        wallet = ctx.policy.wallet(source)
        limit = wallet.max_per_tx_usd if wallet and wallet.max_per_tx_usd is not None else rule.limit_usd
        if total > limit:
            findings.append(
                Finding(
                    "max-per-tx",
                    rule.severity,
                    f"{ctx.name(source)} would send {_usd(total)} in one request, above its limit of {_usd(limit)}.",
                    items[0][0].path,
                )
            )
    return findings


def destination_allowlist(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.destination_allowlist
    allowed = {entry.address for entry in rule.destinations} | {w.address for w in ctx.policy.wallets}
    findings = []
    for transfer in ctx.transfers:
        wallet = ctx.policy.wallet(transfer.source)
        if wallet is None or wallet.role not in rule.roles or transfer.destination in allowed:
            continue
        findings.append(
            Finding(
                "destination-allowlist",
                rule.severity,
                f"{ctx.name(transfer.source)} is a {wallet.role} wallet and {ctx.name(transfer.destination)} is "
                "not on its list of destinations.",
                transfer.path,
            )
        )
    return findings


def explainable(ctx: Context) -> list[Finding]:
    rule = ctx.policy.rules.explainable
    contracts = {entry.address: entry for entry in rule.contracts}
    findings = []
    for action in ctx.actions:
        message = None
        if isinstance(action, UnknownCall):
            entry = contracts.get(action.target)
            if entry is None or (entry.functions and action.selector not in entry.functions):
                message = (
                    f"Guard cannot explain this transaction: {ctx.name(action.actor)} would call "
                    f"{ctx.name(action.target)} with selector {action.selector}, which Guard does not decode, and the "
                    "contract is not allowlisted for it."
                )
        elif isinstance(action, ContractCreation):
            message = (
                f"Guard cannot explain this transaction: it deploys new code at "
                f"{to_checksum_address(action.address)}, and Guard cannot read code."
            )
        elif isinstance(action, UnknownTypedData):
            entry = contracts.get(action.verifying_contract or "")
            if entry is None or entry.functions:
                target = (
                    f"for {ctx.name(action.verifying_contract)}" if action.verifying_contract else "for no contract"
                )
                message = (
                    f"Guard cannot explain this request: it signs {action.primary_type} typed data {target}, a "
                    "message type Guard does not decode."
                )
        elif isinstance(action, PersonalMessage):
            shown = f' "{action.text}"' if action.text and len(action.text) <= 120 else ""
            message = (
                f"Guard cannot explain this request: it signs a free-form message of {action.size} bytes{shown}, "
                "whose meaning depends on whoever later checks the signature."
            )
        if message:
            findings.append(Finding("explainable", rule.severity, message, action.path))
    return findings


RULES: tuple[tuple[str, Callable[[Context], list[Finding]]], ...] = (
    ("no-eip7702-delegation", no_eip7702_delegation),
    ("no-delegatecall", no_delegatecall),
    ("no-blind-signing", no_blind_signing),
    ("no-upgrade-or-owner-change", no_upgrade_or_owner_change),
    ("no-unlimited-approval", no_unlimited_approval),
    ("approval-allowlist", approval_allowlist),
    ("intent-required", intent_required),
    ("velocity", velocity),
    ("max-per-tx", max_per_tx),
    ("destination-allowlist", destination_allowlist),
    ("explainable", explainable),
)
